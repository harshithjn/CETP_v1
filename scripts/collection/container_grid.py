"""
Orthogonal capability grid via containers (CONFERENCE_PLAN.md item 1).

THE problem this solves. Tier 1 found the project's two-scalar hardware signature is
effectively one-dimensional: bandwidth and multi-core compute correlate at **r = +0.958**
across the five EC2 machines, with 97.9% of variance on one principal component. Every
modelling result is therefore forced, and "are two scalars enough?" is unanswerable.

**That cannot be fixed by renting more instance types at any price.** EC2 instance types are
correlated bundles -- a machine with more vCPUs also has more memory bandwidth. The
correlation is a property of how cloud vendors sell hardware, not of the sample size.

Containers break it. `--cpus=N --memory=M` crosses the two axes by construction, so the
design matrix is orthogonal rather than confounded. A 4x4 grid gives 16 capability
configurations whose axes are uncorrelated by fiat -- a better experimental design than 30
EC2 instances, and free.

Honest caveat, to be stated wherever these numbers appear: a CPU quota is **not** the same as
fewer physical cores. Caches and the memory controller stay shared, so this varies
*capability* without varying *microarchitecture*. That is a limitation and also the point --
it isolates capability from architecture, which real machines never do. Conclusions about
signature dimensionality transfer; conclusions about absolute runtime on real small machines
do not.

Procedure: load TPC-H SF1 once into a Docker volume, then restart the same volume under each
resource limit in turn and collect. One PGDATA, so no extra disk per configuration.

  python3 scripts/collection/container_grid.py --setup      # load the volume (once)
  python3 scripts/collection/container_grid.py --collect    # sweep the grid
  python3 scripts/collection/container_grid.py --teardown
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
CSV_DIR = PROJECT_DIR / "scripts" / "collection" / "tpch_data"
OUT_DIR = PROJECT_DIR / "data" / "container_grid"
IMAGE = "postgres:15"
VOLUME = "cetp-pgdata"
NAME = "cetp-pg"
PORT = 55432
PGPASS = "cetp"

# The Docker Desktop VM on this machine has ~4 GB, so memory levels stop below that; the
# CPU axis spans the full 8 cores. Crossed, not confounded.
CPUS = [1, 2, 4, 8]
MEMS = ["1g", "3g"]
# 512m was dropped: with shared_buffers=128MB plus work_mem, TPC-H SF1 does not fit and the
# cell measures OOM-retry behaviour rather than capability. 4x2 is still orthogonal.


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def docker(*args, check=True, timeout=None):
    r = sh(["docker", *args], timeout=timeout)
    if check and r.returncode != 0:
        sys.exit(f"docker {' '.join(args[:3])} failed:\n{r.stderr[:600]}")
    return r


def vm_facts():
    r = docker("info", "--format", "{{.NCPU}} {{.MemTotal}}")
    ncpu, mem = r.stdout.split()
    return int(ncpu), int(mem)


def wait_ready(timeout=180):
    for _ in range(timeout):
        r = sh(["docker", "exec", NAME, "pg_isready", "-U", "postgres", "-q"])
        if r.returncode == 0:
            return True
        time.sleep(1)
    return False


def start(cpus=None, memory=None, extra=()):
    docker("rm", "-f", NAME, check=False)
    args = ["run", "-d", "--name", NAME, "-p", f"{PORT}:5432",
            "-e", f"POSTGRES_PASSWORD={PGPASS}",
            "-v", f"{VOLUME}:/var/lib/postgresql/data",
            "-v", f"{CSV_DIR}:/csv:ro"]
    if cpus is not None:
        args += ["--cpus", str(cpus)]
    if memory is not None:
        # Pin swap to the memory limit so the limit is real rather than spill-to-swap.
        args += ["--memory", memory, "--memory-swap", memory]
    args += list(extra) + [IMAGE,
                           # Keep planner-relevant settings identical to the host cluster so
                           # the only thing varying across the grid is capability.
                           "-c", "shared_buffers=128MB",
                           "-c", "effective_cache_size=4GB",
                           "-c", "work_mem=4MB",
                           "-c", "max_parallel_workers_per_gather=2",
                           "-c", "random_page_cost=4",
                           "-c", "max_connections=50"]
    docker(*args)
    if not wait_ready():
        logs = docker("logs", "--tail", "25", NAME, check=False).stdout
        sys.exit(f"container never became ready:\n{logs[-1500:]}")


def psql_in(sql, db="tpch", quiet=True):
    r = sh(["docker", "exec", "-e", f"PGPASSWORD={PGPASS}", NAME,
            "psql", "-U", "postgres", "-d", db, "-tA", "-c", sql])
    if r.returncode != 0 and not quiet:
        print(r.stderr[:300])
    return r.stdout.strip()


def setup():
    ncpu, mem = vm_facts()
    print(f"Docker VM: {ncpu} CPUs, {mem / 1e9:.1f} GB")
    if max(CPUS) > ncpu:
        print(f"WARNING: grid asks for {max(CPUS)} CPUs but the VM has {ncpu}; "
              f"the top CPU level will not be distinguishable from {ncpu}.")
    docker("volume", "rm", "-f", VOLUME, check=False)
    docker("volume", "create", VOLUME)
    print("starting container unconstrained for the load")
    start()
    psql_in("select 1", db="postgres")
    sh(["docker", "exec", "-e", f"PGPASSWORD={PGPASS}", NAME,
        "createdb", "-U", "postgres", "tpch"])

    # Schema from the host cluster, so the container's schema and indexes are identical.
    schema = Path("/tmp/cetp_tpch_schema.sql")
    if not schema.exists():
        r = sh(["/opt/homebrew/opt/postgresql@15/bin/pg_dump", "-p", "5432", "-d", "tpch",
                "--schema-only", "--no-owner", "--no-privileges"])
        if r.returncode != 0:
            sys.exit(f"could not dump host schema: {r.stderr[:300]}")
        schema.write_text(r.stdout)
    ddl = schema.read_text()
    no_idx = "\n".join(l for l in ddl.splitlines()
                       if not l.lower().startswith(("create index", "create unique index")))
    idx = "\n".join(l for l in ddl.splitlines()
                    if l.lower().startswith(("create index", "create unique index")))
    sh(["docker", "exec", "-i", "-e", f"PGPASSWORD={PGPASS}", NAME,
        "psql", "-U", "postgres", "-d", "tpch", "-q"], input=no_idx)

    print("loading CSVs")
    for t in ["region", "nation", "customer", "supplier", "part", "partsupp",
              "orders", "lineitem"]:
        r = sh(["docker", "exec", "-e", f"PGPASSWORD={PGPASS}", NAME, "psql",
                "-U", "postgres", "-d", "tpch", "-q", "-c",
                f"\\copy {t} from '/csv/{t}.csv' with (format csv, delimiter '|', quote '\"')"])
        n = psql_in(f"select count(*) from {t}")
        print(f"  {t:<12} {n}")
        if r.returncode != 0:
            print(f"    {r.stderr.strip()[:200]}")
    print("building indexes")
    sh(["docker", "exec", "-i", "-e", f"PGPASSWORD={PGPASS}", NAME,
        "psql", "-U", "postgres", "-d", "tpch", "-q"], input=idx)
    psql_in("analyze")
    print(f"  lineitem: {psql_in('select count(*) from lineitem')}")
    print(f"  indexes:  {psql_in(chr(39).join(['select count(*) from pg_indexes where schemaname=', 'public', '']))}")
    print(f"  size:     {psql_in(chr(39).join(['select pg_size_pretty(pg_database_size(', 'tpch', '))']))}")
    docker("rm", "-f", NAME, check=False)
    print("volume loaded; container removed. Run with --collect next.")


def collect(reps, queries, only=None):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ncpu, _ = vm_facts()
    grid = [(c, m) for c in CPUS for m in MEMS]
    print(f"grid: {len(grid)} configurations ({len(CPUS)} CPU x {len(MEMS)} memory)")
    manifest = []
    for i, (c, m) in enumerate(grid, 1):
        mid = f"c{c}m{m}"
        out = OUT_DIR / f"tpch_{mid}.csv"
        if out.exists() and (OUT_DIR / "operator_signature_grid.json").exists() and \
                mid in json.loads((OUT_DIR / "operator_signature_grid.json").read_text()):
            print(f"[{i}/{len(grid)}] {mid} already collected (timings + probe), skipping")
            manifest.append({"machine_id": mid, "cpus": c, "memory": m,
                             "out": str(out.relative_to(PROJECT_DIR))})
            continue
        print(f"[{i}/{len(grid)}] starting {mid}")
        start(cpus=c, memory=m)

        # Operator capability signature, measured under this cell's own quota. This is the
        # whole point of the grid: the signature and the timings must come from the same
        # constrained environment or the ratio between cells means nothing.
        probe_reg = OUT_DIR / "operator_signature_grid.json"
        pr = subprocess.run(
            [sys.executable, str(PROJECT_DIR / "scripts/collection/operator_probe.py"),
             "--machine-id", mid, "--db", "tpch", "--port", str(PORT),
             "--host", "localhost", "--user", "postgres",
             "--registry", str(probe_reg)],
            env={"PGPASSWORD": PGPASS, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
            capture_output=True, text=True)
        if pr.returncode != 0:
            print(f"  operator probe FAILED:\n{pr.stderr[-700:]}")
        else:
            for line in pr.stderr.strip().splitlines():
                if "tuples/s" in line or "WARNING" in line:
                    print("   " + line.strip())

        cmd = [sys.executable, str(PROJECT_DIR / "scripts/collection/collect.py"),
               "--workload", "tpch", "--queries", queries, "--db", "tpch",
               "--machine-id", mid, "--reps", str(reps), "--concurrency", "1",
               "--scale-factor", "SF1", "--port", str(PORT), "--host", "localhost",
               "--out", str(out), "--plans-dir", str(PROJECT_DIR / "data/plans_grid"),
               "--timeout-ms", "600000"]
        if only:
            cmd += ["--only", only]
        env = {"PGPASSWORD": PGPASS, "PATH": "/usr/bin:/bin:/usr/local/bin",
               "PGUSER": "postgres"}
        r = subprocess.run(cmd, env={**env}, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  collection failed:\n{r.stdout[-800:]}\n{r.stderr[-800:]}")
        else:
            print("  " + (r.stdout.strip().splitlines() or ["?"])[-1])
        manifest.append({"machine_id": mid, "cpus": c, "memory": m,
                         "out": str(out.relative_to(PROJECT_DIR))})
        docker("rm", "-f", NAME, check=False)
    (OUT_DIR / "grid_manifest.json").write_text(json.dumps(
        {"image": IMAGE, "vm_cpus": ncpu, "cpu_levels": CPUS, "memory_levels": MEMS,
         "note": "CPU quota is not the same as fewer physical cores; caches and the memory "
                "controller remain shared. Varies capability, not microarchitecture.",
         "configs": manifest}, indent=2) + "\n")
    print(f"\nmanifest: {OUT_DIR / 'grid_manifest.json'}")


def probe_only(force=False):
    """Re-probe every grid cell without redoing collection.

    Needed because the operator probe gained a `parallel_speedup` axis mid-run: cells
    probed with operator_rates_v1 cannot be used, since v1 under-predicted a
    CPU-constrained machine by 64% (1.08x against a true 3.01x) for want of that axis.
    """
    reg_path = OUT_DIR / "operator_signature_grid.json"
    reg = json.loads(reg_path.read_text()) if reg_path.exists() else {}
    grid = [(c, m) for c in CPUS for m in MEMS]
    todo = []
    for c, m in grid:
        mid = f"c{c}m{m}"
        kind = reg.get(mid, {}).get("signature_kind")
        if force or kind != "operator_rates_v2":
            todo.append((c, m, mid, kind))
    if not todo:
        print("every cell already carries operator_rates_v2")
        return
    print(f"re-probing {len(todo)} cell(s): "
          + ", ".join(f"{mid}({kind})" for _, _, mid, kind in todo))
    for c, m, mid, _ in todo:
        print(f"  {mid}: starting container")
        start(cpus=c, memory=m)
        r = subprocess.run(
            [sys.executable, str(PROJECT_DIR / "scripts/collection/operator_probe.py"),
             "--machine-id", mid, "--db", "tpch", "--port", str(PORT),
             "--host", "localhost", "--user", "postgres", "--registry", str(reg_path)],
            env={"PGPASSWORD": PGPASS,
                 "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"    FAILED:\n{r.stderr[-600:]}")
        else:
            for line in r.stderr.strip().splitlines():
                if "tuples/s" in line or "speedup" in line or "WARNING" in line:
                    print("    " + line.strip())
        docker("rm", "-f", NAME, check=False)
    print(f"\nregistry: {reg_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--teardown", action="store_true")
    ap.add_argument("--reps", type=int, default=4)
    ap.add_argument("--queries", default="scripts/collection/queries")
    ap.add_argument("--only", default=None)
    ap.add_argument("--probe-only", action="store_true",
                    help="re-run the operator probe per cell without redoing collection")
    ap.add_argument("--force-probe", action="store_true",
                    help="with --probe-only, re-probe even cells already on v2")
    args = ap.parse_args()
    if args.setup:
        setup()
    if args.collect:
        collect(args.reps, args.queries, args.only)
    if args.probe_only:
        probe_only(force=args.force_probe)
    if args.teardown:
        docker("rm", "-f", NAME, check=False)
        docker("volume", "rm", "-f", VOLUME, check=False)
        print("container and volume removed")
    if not (args.setup or args.collect or args.teardown or args.probe_only):
        ap.print_help()


if __name__ == "__main__":
    main()
