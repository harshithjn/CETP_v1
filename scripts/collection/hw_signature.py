"""
Canonical hardware signature: measure it, validate it, register it
(CONFERENCE_PLAN.md item 2).

This REPLACES the project's original two-scalar signature, for two reasons:

1. **The STREAM-triad bandwidth probe is lost.** It exists nowhere in the repository, so the
   published bandwidth figures (8.44-14.9) cannot be reproduced and **no new machine can be
   placed on that axis at all**. That blocked the laptop-as-dev experiment from even
   evaluating the project's own `naive-linear` baseline.
2. **The compute axis was probed at `nproc`**, but a query can use only
   `1 + max_parallel_workers_per_gather`. Invisible at 2-4 cores, catastrophic at 8
   (324% vs 153% MAPE -- results/tier1_laptop_dev.md). It is a hardware x configuration
   artifact, not a hardware property.

The replacement is `hw_probe_suite.c`: seven axes instead of two, each mapped to a resource a
query executor actually consumes, and probed at BOTH `nproc` (for comparability with the old
collection) and the achievable DOP (the figure a cost model should use).

Signatures are written to a registry, `models/hardware_signatures.json`, keyed by machine id,
with the probe's own version, the host facts and the PostgreSQL settings that determine the
achievable DOP. A signature without those is not reproducible, which is how the last one got
lost.

Usage:
  python3 scripts/collection/hw_signature.py --machine-id m1 --db tpch
  python3 scripts/collection/hw_signature.py --list
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parent / "hw_probe_suite.c"
BIN = Path("/tmp/cetp_hw_probe_suite")
REGISTRY = PROJECT_DIR / "models" / "hardware_signatures.json"

# Physically implausible values mean the probe was optimised away or the machine was busy.
PLAUSIBLE = {
    "int_scalar_gops": (0.05, 500.0),
    "fp_scalar_gflops": (0.02, 100.0),
    "fp_vector_gflops": (0.1, 1000.0),
    "fp_vector_gflops_nproc": (0.1, 10000.0),
    "branch_cost_ns": (0.005, 50.0),
    "cache_l1_ns": (0.1, 50.0),
    "cache_l2_ns": (0.5, 200.0),
    "cache_l3_ns": (1.0, 500.0),
    "mem_latency_ns": (10.0, 2000.0),
    "mem_bandwidth_gbs_1t": (0.1, 500.0),
    "mem_bandwidth_gbs_mt": (0.1, 2000.0),
}


def build():
    if BIN.exists() and BIN.stat().st_mtime > SRC.stat().st_mtime:
        return
    subprocess.run(["cc", "-O2", "-o", str(BIN), str(SRC), "-lpthread"], check=True)


def pg_facts(db, port):
    """The achievable DOP is a PostgreSQL setting, not a hardware property -- so it has to
    be recorded alongside the signature or the signature cannot be interpreted."""
    out = {}
    for s in ("server_version", "max_parallel_workers_per_gather",
              "max_parallel_workers", "max_worker_processes", "shared_buffers", "work_mem"):
        try:
            r = subprocess.run(["psql", "-p", str(port), "-d", db, "-tAc", f"show {s}"],
                               capture_output=True, text=True, timeout=15)
            out[s] = r.stdout.strip() or None
        except (subprocess.TimeoutExpired, OSError):
            out[s] = None
    try:
        out["achievable_dop"] = 1 + int(out["max_parallel_workers_per_gather"])
    except (TypeError, ValueError):
        out["achievable_dop"] = None
    return out


def host_facts():
    f = {"arch": platform.machine(), "platform": platform.platform(),
         "identity_source": "platform"}
    try:
        f["nproc"] = int(subprocess.run(["getconf", "_NPROCESSORS_ONLN"],
                                        capture_output=True, text=True).stdout.strip())
    except (ValueError, OSError):
        f["nproc"] = os.cpu_count()
    if platform.system() == "Darwin":
        for key, sc in (("cpu_brand", "machdep.cpu.brand_string"),
                        ("mem_bytes", "hw.memsize"),
                        ("perf_cores", "hw.perflevel0.logicalcpu"),
                        ("eff_cores", "hw.perflevel1.logicalcpu")):
            r = subprocess.run(["sysctl", "-n", sc], capture_output=True, text=True)
            v = r.stdout.strip()
            f[key] = int(v) if v.isdigit() else (v or None)
    # On EC2 this should be IMDSv2-confirmed; the old script inferred identity from nproc,
    # which cannot distinguish instance types sharing a vCPU count.
    try:
        tok = subprocess.run(["curl", "-sX", "PUT", "http://169.254.169.254/latest/api/token",
                              "-H", "X-aws-ec2-metadata-token-ttl-seconds: 60"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        if tok:
            for k in ("instance-type", "instance-id"):
                v = subprocess.run(["curl", "-s", "-H", f"X-aws-ec2-metadata-token: {tok}",
                                    f"http://169.254.169.254/latest/meta-data/{k}"],
                                   capture_output=True, text=True, timeout=2).stdout.strip()
                if v:
                    f[k.replace("-", "_")] = v
                    f["identity_source"] = "imdsv2"
    except (subprocess.TimeoutExpired, OSError):
        pass
    return f


def probe(seconds, nproc_threads, dop):
    build()
    r = subprocess.run([str(BIN), str(seconds), str(nproc_threads), str(dop)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"probe failed: {r.stderr[:300]}")
    return json.loads(r.stdout)


def validate(sig):
    bad = []
    for k, (lo, hi) in PLAUSIBLE.items():
        v = sig.get(k)
        if v is None:
            continue
        if not (lo <= v <= hi):
            bad.append(f"{k}={v} outside [{lo}, {hi}]")
    # The cache/DRAM hierarchy must be monotonic; if it is not, the pointer chase is broken.
    lat = [sig.get(k) for k in ("cache_l1_ns", "cache_l2_ns", "cache_l3_ns", "mem_latency_ns")]
    if all(x is not None for x in lat) and lat != sorted(lat):
        bad.append(f"latency hierarchy not monotonic: {lat}")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machine-id")
    ap.add_argument("--db", default="tpch")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--dop", type=int, default=None,
                    help="override the achievable DOP (default: read it from PostgreSQL)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--note", default=None)
    args = ap.parse_args()

    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {
        "schema": "cetp-hardware-signature/2", "machines": {}}

    if args.list:
        m = registry.get("machines", {})
        if not m:
            print("registry is empty")
            return
        print(f"{'machine':<12}{'arch':<10}{'nproc':>6}{'dop':>5}"
              f"{'fp_vec_dop':>12}{'bw_mt':>9}{'mem_ns':>9}  probed")
        for k, v in sorted(m.items()):
            p, h = v.get("probe", {}), v.get("host", {})
            print(f"{k:<12}{h.get('arch', '?'):<10}{h.get('nproc', '?'):>6}"
                  f"{v.get('postgres', {}).get('achievable_dop', '?'):>5}"
                  f"{p.get('fp_vector_gflops_dop', float('nan')):>12.2f}"
                  f"{p.get('mem_bandwidth_gbs_mt', float('nan')):>9.2f}"
                  f"{p.get('mem_latency_ns', float('nan')):>9.1f}  {v.get('probed_at', '?')}")
        return

    if not args.machine_id:
        sys.exit("--machine-id is required (or use --list)")

    host = host_facts()
    pg = pg_facts(args.db, args.port)
    dop = args.dop if args.dop is not None else (pg.get("achievable_dop") or 3)
    nproc = host.get("nproc") or 4
    print(f"probing {args.machine_id}: arch={host.get('arch')} nproc={nproc} "
          f"achievable_dop={dop} ({args.seconds}s per axis)")
    sig = probe(args.seconds, nproc, dop)

    problems = validate(sig)
    entry = {
        "probe": sig, "host": host, "postgres": pg,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "probe_source_sha": subprocess.run(
            ["shasum", "-a", "256", str(SRC)], capture_output=True,
            text=True).stdout.split()[0] if SRC.exists() else None,
        "validation": {"ok": not problems, "problems": problems},
    }
    if args.note:
        entry["note"] = args.note

    registry.setdefault("machines", {})[args.machine_id] = entry
    REGISTRY.parent.mkdir(exist_ok=True)
    REGISTRY.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")

    for k in ("int_scalar_gops", "fp_scalar_gflops", "fp_vector_gflops",
              "fp_vector_gflops_dop", "fp_vector_gflops_nproc", "branch_cost_ns",
              "cache_l1_ns", "cache_l2_ns", "cache_l3_ns", "mem_latency_ns",
              "mem_bandwidth_gbs_1t", "mem_bandwidth_gbs_mt"):
        if k in sig:
            print(f"  {k:<26} {sig[k]}")
    if problems:
        print("\nVALIDATION PROBLEMS (signature recorded, but do not trust it):")
        for b in problems:
            print(f"  - {b}")
    else:
        print("\nvalidation passed (plausible ranges + monotonic latency hierarchy)")
    print(f"registered in {REGISTRY.relative_to(PROJECT_DIR)}")


if __name__ == "__main__":
    main()
