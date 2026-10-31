"""
In-SQL operator capability probe.

WHY THIS EXISTS. The project's hardware signature is two scalars from a C microbenchmark:
STREAM-triad bandwidth and FP-vector GFLOPS. Three measured problems with it:

  1. It does not cross an ISA boundary. Laptop(arm64) -> EC2(x86_64) scores 324% MAPE,
     153% after the DOP fix, against 123% for assuming prod == dev. The signature makes
     the prediction WORSE than doing nothing (results/tier1_laptop_dev.md).
  2. Its two axes are not two axes. r = +0.958 across the five EC2 machines, 97.9% of
     variance on one principal component, and it tracks core count at r = +0.841
     (results/tier1_hw_signature.md). It is a "how big is this machine" proxy.
  3. FP-vector GFLOPS is the wrong quantity. TPC-H is hash-table probes, sorts,
     aggregation and sequential scans -- integer and memory work. Nothing in the
     workload issues the vector FP instructions the probe measures.

WHAT THIS DOES INSTEAD. It measures the engine doing the actual operators, in SQL:

  seqscan   tuples/s through the scan + filter path
  hashjoin  tuples/s to build and probe a hash table
  sort      tuples/s through the sort path
  hashagg   tuples/s through hash aggregation
  nestloop  tuples/s through indexed nested-loop lookup (random access + TLB)

Three consequences, each of which fixes one of the problems above:

  * It crosses ISAs by construction, because it is the same engine running the same code
    path, not a proxy quantity that happens to correlate on x86.
  * It runs on MANAGED Postgres -- RDS, Aurora, Cloud SQL -- because it is only SQL. The
    C probe needs a shell and a compiler, which is why the current signature cannot
    describe the machines most production Postgres actually runs on.
  * The axes decouple. Measured on PostgreSQL 15 / Apple M1, dropping work_mem from 64MB
    to 1MB left seqscan at 1.049x and cut hashjoin to 0.580x -- a 1.81x spread that no
    single capability scalar can represent.

HOW IT IS MEANT TO BE USED. Not as a replacement scalar. Per-operator rates let the
prediction be per-operator:

    T_prod = sum over plan nodes of  t_dev(node) * rate_dev(op) / rate_prod(op)

which is the only form that can express "this machine is fast at scans and slow at
sorts" -- the thing a single ratio cannot say.

CAVEATS, to be stated wherever these numbers appear:
  * Rates are tuples/s for THIS probe's tuple widths and cardinalities. They are
    capability ratios between machines, not absolute operator throughputs.
  * Parallelism is disabled (max_parallel_workers_per_gather=0) so the probe measures
    one worker. Compare machines at matched DOP, or probe again with parallelism on.
  * JIT is disabled. With jit=on, PG may compile expressions and the rate stops being a
    property of the hardware alone.
  * EXPLAIN ANALYZE adds per-tuple timing instrumentation. The rates are therefore
    *instrumented* rates. That is fine for a ratio between machines measured the same
    way; it is not a throughput you should quote in isolation.
  * work_mem is pinned, because sort and hashagg rates depend on it strongly (above).
    Two machines probed at different work_mem are not comparable. Note that the pinned
    probe value need NOT equal the value the real workload runs at: the probe's job is to
    place machines on a common capability scale, which requires the setting be the SAME
    everywhere, not that it match production. A probe that inherited each server's own
    work_mem would confound capability with configuration.
  * A CPU quota does not slow a SINGLE-THREADED operator down -- one worker needs one
    core. So on a CPU-quota grid the five serial axes are nearly constant while
    `parallel_speedup` moves. Varying the serial axes requires genuinely different
    hardware (a different ISA, cache hierarchy or memory subsystem), not a quota.

  python3 scripts/collection/operator_probe.py --machine-id prod-db-1 --db mydb
  python3 scripts/collection/operator_probe.py --machine-id m1 --db postgres --json out.json
"""

import argparse
import json
import platform
import random
import statistics
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
REGISTRY = PROJECT_DIR / "models" / "operator_signature.json"

# Probe table row counts. Keep fixed across machines, and keep them LARGE ENOUGH that a
# single measurement runs for a few hundred milliseconds. At 400k/100k the parallel scan
# finished in 18 ms, so parallel-worker startup (a few ms) was a large fraction of the
# measurement and the derived speedup came back at cv = 0.50 -- including an impossible
# 1.81x on a 1-CPU quota. Longer runs amortise startup and settle the variance.
SCALE_A = 2_000_000
SCALE_B = 400_000
# I/O axis. The five operator axes above all run against tables that fit in shared_buffers,
# so they measure compute, memory and parallel scheduling and NOTHING about storage. That
# gap was diagnosed as the cause of the residual bias in laptop->production prediction: the
# probe claimed the host's seqscan was faster than a container's (11.6M against 9.6M
# tuples/s) while the real workload said the opposite, because TPC-H q1/q6 read ~850 MB past
# a 128 MB buffer pool. Over-prediction correlated with each query's I/O fraction at
# Spearman r = +0.302.
#
# SCALE_IO exceeds shared_buffers (128 MB) several times over, so the scan cannot be served
# from the buffer pool. It is a separate table because enlarging the main probe tables this
# far would slow every other axis without making them more informative.
#
# !! THIS AXIS DOES NOT MEASURE WHAT IT WAS BUILT TO MEASURE. !!
#
# It was added to capture storage read throughput, the diagnosed cause of the residual bias
# in laptop->production prediction. Measured across the 8-cell container grid, it does not
# isolate storage:
#
#   Spearman(io/seqscan ratio, CPU quota) = +0.762
#   Spearman(io/seqscan ratio, memory)    = +0.548
#
# A storage axis would track memory (page-cache headroom). This tracks the CPU quota more
# strongly, because at 6M rows the scan is ~3x the work of the `seqscan` axis and therefore
# ~3x more exposed to CFS throttling. On the host, where there is no quota and the table fits
# the OS page cache, it is flat against `seqscan` (12.56M against 12.77M tuples/s). So this
# is a SUSTAINED-THROUGHPUT axis, not an I/O axis, and `cetp.operator_model` deliberately
# does NOT use it.
#
# A real I/O axis needs a probe table larger than RAM (>8 GB on this host, >4 GB inside the
# Docker VM). That needs roughly 10 GB of free disk and this machine has 7. Gap 3 is
# therefore blocked by DISK, the same thing blocking SF10 -- not by design.
SCALE_IO = 6_000_000
WORK_MEM = "64MB"
# 5 repetitions leaves only 4 warm samples, and the bootstrap SE of a median over 4 points
# is coarse: it failed 4 of 8 grid cells at 0.083-0.178 against a 0.08 gate even though the
# medians themselves were reproducible to 2-3% between passes. 9 gives 8 warm samples.
REPS = 9                   # first rep discarded as warmup
# The parallel axis is measurably noisier than the serial ones -- worker startup, leader
# participation and page-cache state all move it, and it was observed at cv=0.37 against
# 0.005-0.04 for the serial axes. Since `parallel_speedup` multiplies every parallel node
# in the model, it gets more repetitions than the rest.
REPS_PER_PROBE = {"parallel_seqscan": 11, "io_seqscan": 3}

# Repeatability gate. A capability probe is only a capability measurement if the machine
# was otherwise idle; on a busy machine it measures contention instead. Observed directly:
# probing this host while a container sweep ran on it moved seqscan's cv from 0.008 to
# 0.337 and the derived parallel speedup from 2.47x to 1.66x -- a 48% swing on an axis that
# multiplies every parallel node in the model. Above this threshold the record is flagged
# unreliable and consumers refuse it, rather than quietly shipping a contended number.
# WHAT TO GATE ON, and the first version of this got it wrong. The reported rate is a
# MEDIAN over warm repetitions, so what matters is how precisely that median is pinned down,
# not how spread the individual runs are. Measured on an idle host over 3 independent passes:
#
#   axis              within-pass cv    between-pass cv
#   parallel_seqscan      0.587             0.027
#   seqscan               0.041             0.022
#
# Parallel runs jitter enormously run-to-run (worker scheduling) while their median is
# reproducible to 3%. Gating on cv therefore failed 4 of 8 grid cells for no reason, the
# usable population shifted between passes, and headline numbers moved 5-15pp because of it.
# The gate now uses the bootstrap standard error of the median, relative to the median.
MAX_MEDIAN_SE = 0.08
# cv is still recorded, as information rather than as a gate.
MAX_CV = 0.15
# Degree of parallelism for the parallel axis: must match the DOP the workload actually
# runs at (1 + max_parallel_workers_per_gather), or the speedup measured is not the
# speedup the queries will see.
PARALLEL_DOP = 3

PRESET = (
    f"SET max_parallel_workers_per_gather=0; SET jit=off; SET work_mem='{WORK_MEM}';"
)

SETUP = f"""
DROP TABLE IF EXISTS _cetp_probe_a, _cetp_probe_b;
CREATE UNLOGGED TABLE _cetp_probe_a AS
  SELECT g AS id, (g::bigint*7919)%1000003 AS k, repeat('x',40) AS pad
  FROM generate_series(1,{SCALE_A}) g;
CREATE UNLOGGED TABLE _cetp_probe_b AS
  SELECT g AS id, (g::bigint*104729)%1000003 AS k
  FROM generate_series(1,{SCALE_B}) g;
CREATE INDEX _cetp_probe_b_k ON _cetp_probe_b(k);
DROP TABLE IF EXISTS _cetp_probe_io;
CREATE UNLOGGED TABLE _cetp_probe_io AS
  SELECT g AS id, (g::bigint*7919)%1000003 AS k, repeat('y',48) AS pad
  FROM generate_series(1,{SCALE_IO}) g;
ANALYZE _cetp_probe_a;
ANALYZE _cetp_probe_b;
ANALYZE _cetp_probe_io;
"""

TEARDOWN = ("DROP TABLE IF EXISTS _cetp_probe_a, _cetp_probe_b, _cetp_probe_io;")

# name -> (sql, tuples the operator actually moves, extra session settings)
# The extra settings exist to FORCE the named operator. Without them the planner is free
# to pick something else and the rate silently describes a different operator -- which is
# exactly what the validity check below caught on the first run of the nestloop probe.
PROBES = {
    "seqscan":  ("SELECT count(*) FROM _cetp_probe_a WHERE k >= 0", SCALE_A, ""),
    "hashjoin": ("SELECT count(*) FROM _cetp_probe_a a JOIN _cetp_probe_b b ON a.k = b.k",
                 SCALE_A + SCALE_B, "SET enable_mergejoin=off; SET enable_nestloop=off;"),
    "sort":     (f"SELECT k FROM _cetp_probe_a ORDER BY k OFFSET {SCALE_A - 1}", SCALE_A, ""),
    "hashagg":  (f"SELECT k, count(*) FROM _cetp_probe_a GROUP BY k "
                 f"OFFSET {SCALE_A * 10}", SCALE_A, ""),
    "nestloop": ("SELECT count(*) FROM _cetp_probe_b b JOIN _cetp_probe_b i ON i.k = b.k",
                 SCALE_B, "SET enable_hashjoin=off; SET enable_mergejoin=off; "
                          "SET enable_material=off;"),
    # PARALLEL AXIS. The five axes above are deliberately single-worker, so they measure
    # per-worker capability. They are therefore BLIND to how much parallel speedup a
    # machine can actually deliver -- and that blindness is not academic: predicting the
    # 1-CPU container from the 8-core host, the single-worker axes said 1.29x while the
    # real runtime ratio was 3.01x, because the container's 3 workers timeshare one CPU
    # whereas the host's run concurrently. This is the nproc-vs-achievable-DOP bug in a
    # new form, and a per-operator model without this axis reproduces it.
    #
    # Same scan as `seqscan` but with parallelism allowed. The ratio
    # parallel_seqscan / seqscan is the speedup the machine actually grants, which is what
    # the model needs for nodes under a Gather.
    # Storage read throughput: a table far larger than shared_buffers, so the scan must
    # come off disk rather than out of the buffer pool.
    # Named io_seqscan for continuity with collected data; it is a sustained-throughput
    # axis, not a storage axis. See the note on SCALE_IO.
    "io_seqscan": ("SELECT count(*) FROM _cetp_probe_io WHERE k >= 0", SCALE_IO, ""),
    "parallel_seqscan": ("SELECT count(*) FROM _cetp_probe_a WHERE k >= 0", SCALE_A,
                         f"SET max_parallel_workers_per_gather={PARALLEL_DOP - 1}; "
                         f"SET parallel_setup_cost=0; SET parallel_tuple_cost=0; "
                         f"SET min_parallel_table_scan_size=0;"),
}


CONN = {"host": None, "user": None}          # set from argv in main()


def psql(db, port, sql, tuples_only=True):
    cmd = ["psql", "-p", str(port), "-d", db, "-v", "ON_ERROR_STOP=1"]
    if CONN["host"]:
        cmd += ["-h", CONN["host"]]
    if CONN["user"]:
        cmd += ["-U", CONN["user"]]
    cmd += ["-tAqc", sql] if tuples_only else ["-qc", sql]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()[:400]}")
    return r.stdout


def measure(db, port, sql, n_tuples, extra="", reps=None):
    """Median warm execution time -> tuples/s, plus the node types actually used."""
    reps = reps or REPS
    times, node_types = [], None
    for i in range(reps):
        out = psql(db, port, f"{PRESET} {extra} EXPLAIN (ANALYZE, FORMAT JSON) {sql}")
        plan = json.loads(out)[0]
        times.append(plan["Execution Time"])
        if i == 0:
            node_types = sorted(collect_node_types(plan["Plan"]))
    warm = sorted(times[1:])                      # discard rep 0 (cold)
    med = statistics.median(warm)
    return {
        "median_ms": med,
        "min_ms": min(warm),
        "tuples_per_s": n_tuples / (med / 1000.0),
        "cv": (statistics.stdev(warm) / statistics.mean(warm)) if len(warm) > 1 else 0.0,
        # Precision of the MEDIAN, which is the statistic actually reported.
        "median_se_rel": median_se_rel(warm),
        "n_warm": len(warm),
        "node_types": node_types,
    }


def median_se_rel(samples, n_boot=2000, seed=42):
    """Bootstrap standard error of the median, divided by the median."""
    if len(samples) < 2:
        return 0.0
    rng = random.Random(seed)
    meds = []
    for _ in range(n_boot):
        draw = [samples[rng.randrange(len(samples))] for _ in samples]
        meds.append(statistics.median(draw))
    m = statistics.median(samples)
    return statistics.pstdev(meds) / m if m > 0 else 0.0


def collect_node_types(node, acc=None):
    acc = set() if acc is None else acc
    acc.add(node.get("Node Type", "?"))
    for child in node.get("Plans", []) or []:
        collect_node_types(child, acc)
    return acc


def verify_operator_used(name, node_types):
    """A probe is only valid if the planner actually chose the operator it names."""
    expect = {
        "seqscan": "Seq Scan", "hashjoin": "Hash Join", "sort": "Sort",
        "hashagg": "Aggregate", "nestloop": "Nested Loop",
        # A parallel scan must actually have been parallelised, or the "speedup" is 1.0
        # for the wrong reason and would be silently reported as a capability.
        "parallel_seqscan": "Gather",
        "io_seqscan": "Seq Scan",
    }[name]
    return expect in node_types


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machine-id", required=True)
    ap.add_argument("--db", default="postgres")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--json", help="also write the raw result here")
    ap.add_argument("--keep", action="store_true", help="do not drop the probe tables")
    ap.add_argument("--host", default=None, help="server host (omit for a local socket)")
    ap.add_argument("--user", default=None)
    ap.add_argument("--registry", default=None,
                    help="write to this registry instead of models/operator_signature.json")
    args = ap.parse_args()
    CONN["host"], CONN["user"] = args.host, args.user
    registry = Path(args.registry) if args.registry else REGISTRY

    print(f"probing {args.machine_id} via SQL on port {args.port} "
          f"(no shell or compiler needed on the server)", file=sys.stderr)
    psql(args.db, args.port, SETUP, tuples_only=False)

    server_version = psql(args.db, args.port, "SHOW server_version").strip()
    rates, detail, invalid = {}, {}, []
    try:
        for name, (sql, n, extra) in PROBES.items():
            m = measure(args.db, args.port, sql, n, extra,
                        reps=REPS_PER_PROBE.get(name, REPS))
            ok = verify_operator_used(name, m["node_types"])
            if not ok:
                invalid.append(name)
            rates[name] = m["tuples_per_s"]
            detail[name] = m
            flag = "" if ok else "  <- OPERATOR NOT USED, rate is not meaningful"
            print(f"  {name:9s} {m['median_ms']:9.2f} ms  "
                  f"{m['tuples_per_s']:14,.0f} tuples/s  cv={m['cv']:.3f}{flag}",
                  file=sys.stderr)
    finally:
        if not args.keep:
            psql(args.db, args.port, TEARDOWN, tuples_only=False)

    speedup = None
    speedup_cv = None
    if "parallel_seqscan" in rates and rates.get("seqscan"):
        speedup = rates["parallel_seqscan"] / rates["seqscan"]
        # Propagated (not measured jointly), so this is an upper bound on the precision of
        # the speedup rather than its exact noise.
        speedup_cv = (detail["parallel_seqscan"]["cv"] ** 2 + detail["seqscan"]["cv"] ** 2) ** 0.5
        print(f"  parallel speedup at DOP {PARALLEL_DOP}: {speedup:.3f}x "
              f"(1.0 means the machine grants no parallelism)", file=sys.stderr)

    # io_seqscan is excluded from the gate: the model does not use it (see SCALE_IO) and
    # its own between-pass reproducibility is 0.427, so gating on it would fail every
    # cell for no benefit.
    imprecise = {k: v["median_se_rel"] for k, v in detail.items()
                 if k != "io_seqscan" and v["median_se_rel"] > MAX_MEDIAN_SE}
    noisy = {k: v["cv"] for k, v in detail.items() if v["cv"] > MAX_CV}

    record = {
        "machine_id": args.machine_id,
        "signature_kind": "operator_rates_v2",
        "max_cv": MAX_CV,
        "max_median_se": MAX_MEDIAN_SE,
        "imprecise_probes": imprecise,
        "io_seqscan_is_not_a_storage_axis":
            "measured across the container grid it tracks CPU quota (Spearman +0.762) more "
            "than memory (+0.548), and is flat against seqscan on an unthrottled host. It "
            "is a sustained-throughput axis. cetp.operator_model does not use it.",
        "noisy_probes": noisy,
        "reliable": not imprecise and not invalid,
        "parallel_speedup": speedup,
        "parallel_speedup_cv": speedup_cv,
        "parallel_dop": PARALLEL_DOP,
        "server_version": server_version,
        "probe_scale": {"table_a_rows": SCALE_A, "table_b_rows": SCALE_B,
                        "work_mem": WORK_MEM, "reps": REPS,
                        "reps_per_probe": REPS_PER_PROBE,
                        "parallel_dop": PARALLEL_DOP, "jit": "disabled"},
        "client_platform": f"{platform.system()} {platform.machine()}",
        "rates_tuples_per_s": rates,
        "detail": detail,
        "invalid_probes": invalid,
        "comparable_only_with": "other operator_rates_v2 records at the same probe_scale",
    }

    if invalid:
        print(f"\nWARNING: {len(invalid)} probe(s) did not use the intended operator "
              f"({', '.join(invalid)}). Those rates must not be used as capability "
              f"ratios.", file=sys.stderr)

    if imprecise:
        print(f"\nWARNING: {len(imprecise)} probe(s) have an imprecise median "
              f"(relative bootstrap SE > {MAX_MEDIAN_SE}): "
              f"{', '.join(f'{k}={v:.3f}' for k, v in imprecise.items())}. "
              f"Add repetitions or quiet the machine.", file=sys.stderr)
    if noisy and not imprecise:
        print(f"\nnote: {len(noisy)} probe(s) exceeded cv={MAX_CV} "
              f"({', '.join(f'{k}={v:.3f}' for k, v in noisy.items())}). The machine was "
              f"but their medians are precise, so the record is usable: run-to-run "
              f"jitter is expected on parallel axes.", file=sys.stderr)

    registry.parent.mkdir(parents=True, exist_ok=True)
    reg = json.loads(registry.read_text()) if registry.exists() else {}
    reg[args.machine_id] = record
    registry.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {registry} ({len(reg)} machine(s) known)", file=sys.stderr)

    if args.json:
        Path(args.json).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(rates, indent=2))


if __name__ == "__main__":
    main()
