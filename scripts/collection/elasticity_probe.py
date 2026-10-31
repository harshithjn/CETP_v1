"""
Resource-elasticity probe (TASKS.md Tier 1, item 17).

Measures, per query, how runtime responds to each of three resources being varied,
so that "what is this query bottlenecked on" becomes a MEASURED quantity instead of
the 21 hand-assigned judgement calls in BOTTLENECK_LABELS.

Axes, all settable per session so the shared PostgreSQL instance is never restarted
and other databases on it are never disturbed:

  dop        max_parallel_workers_per_gather in {0,1,2,4}
             -> parallel compute. The resource recorded is the ACTUAL degree of
                parallelism (1 + workers launched, read from the plan), not the
                requested setting, because the planner often declines to go parallel.

  work_mem   work_mem in {1MB, 4MB, 16MB, 64MB}
             -> memory capacity. Drives whether sorts and hash joins stay in memory
                or spill to disk.

  antagonist membw_antagonist threads in {0, 4, 8}
             -> memory bandwidth. A co-running STREAM-triad process streams through
                a working set far larger than cache, stealing DRAM bandwidth.

Timing matches the original collection protocol exactly: EXPLAIN (ANALYZE, BUFFERS,
FORMAT JSON), taking the server-reported `Execution Time`.

KNOWN LIMITATIONS, both recorded in every output row:
  - No CPU affinity control. macOS has no taskset/cgroups, so the antagonist cannot be
    pinned away from PostgreSQL's cores. It therefore steals some COMPUTE as well as
    bandwidth, and the antagonist axis must be read as "bandwidth + core contention",
    not pure bandwidth. The dop axis varies cores with no bandwidth pressure, which is
    what lets the two be partly separated downstream.
  - shared_buffers cannot be swept without restarting the server, which would disrupt
    the other databases on this instance. Buffer-cache sensitivity is therefore
    approached through work_mem and through the cold/warm first-run effect instead.

Writes results/elasticity_raw.csv incrementally, so a partial run is still usable.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import psycopg2

PROJECT_DIR = Path(__file__).resolve().parents[2]
QUERY_DIR = Path(__file__).resolve().parent / "queries"
RESULTS_DIR = PROJECT_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)
OUT_CSV = RESULTS_DIR / "elasticity_raw.csv"
ANTAGONIST_BIN = Path("/tmp/membw_antagonist")
ANTAGONIST_SRC = Path(__file__).resolve().parent / "membw_antagonist.c"

DB = os.environ.get("CETP_DB", "tpch")
QUERY_NUMS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 21, 22]
# Queries whose reconstruction is only APPROXIMATE -- see queries/README.md.
APPROXIMATE = {16, 20}

WARMUP_RUNS = 1      # repetition 1 of a cell is a cold-cache outlier; always discarded
MEASURED_RUNS = 2    # warm-run CV is 0.1-2.5%, so 2 is ample

BASELINE = {"mpwpg": 2, "work_mem_mb": 4, "antagonist_threads": 0}

CONFIGS = (
    [{"axis": "dop", "mpwpg": d, "work_mem_mb": 4, "antagonist_threads": 0} for d in (0, 1, 2, 4)]
    + [{"axis": "work_mem", "mpwpg": 2, "work_mem_mb": w, "antagonist_threads": 0}
       for w in (1, 16, 64)]
    + [{"axis": "antagonist", "mpwpg": 2, "work_mem_mb": 4, "antagonist_threads": t}
       for t in (4, 8)]
)


def ensure_antagonist():
    if ANTAGONIST_BIN.exists():
        return
    subprocess.run(["cc", "-O2", "-o", str(ANTAGONIST_BIN), str(ANTAGONIST_SRC), "-lpthread"],
                   check=True)


def walk_workers(node, acc):
    acc["workers_launched"] = max(acc["workers_launched"], node.get("Workers Launched", 0) or 0)
    if node.get("Node Type") == "Gather" or node.get("Node Type") == "Gather Merge":
        acc["gather_nodes"] += 1
    acc["shared_hit"] = max(acc["shared_hit"], node.get("Shared Hit Blocks", 0) or 0)
    acc["shared_read"] = max(acc["shared_read"], node.get("Shared Read Blocks", 0) or 0)
    acc["temp_written"] = max(acc["temp_written"], node.get("Temp Written Blocks", 0) or 0)
    for c in node.get("Plans", []):
        walk_workers(c, acc)


def run_once(cur, sql):
    cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
    plan = cur.fetchone()[0][0]
    acc = {"workers_launched": 0, "gather_nodes": 0,
           "shared_hit": 0, "shared_read": 0, "temp_written": 0}
    walk_workers(plan["Plan"], acc)
    return {"time_ms": plan["Execution Time"],
            "planning_ms": plan.get("Planning Time"), **acc}


def load_sql(n):
    return "\n".join(l for l in (QUERY_DIR / f"q{n}.sql").read_text().splitlines()
                     if not l.strip().startswith("--"))


def main():
    ensure_antagonist()
    sqls = {n: load_sql(n) for n in QUERY_NUMS}
    rows = []
    done = set()
    if OUT_CSV.exists():
        prev = pd.read_csv(OUT_CSV)
        rows = prev.to_dict("records")
        done = {(r["query_num"], r["axis"], r["mpwpg"], r["work_mem_mb"],
                 r["antagonist_threads"], r["run_index"]) for r in rows}
        print(f"resuming: {len(done)} measurements already recorded")

    conn = psycopg2.connect(dbname=DB)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("select version()")
    pg_version = cur.fetchone()[0]
    print(pg_version)

    # One global warmup pass so the OS page cache is hot before any measurement.
    print("global warmup pass ...", flush=True)
    cur.execute("set max_parallel_workers_per_gather = 2")
    cur.execute("set work_mem = '4MB'")
    for n in QUERY_NUMS:
        run_once(cur, sqls[n])
    print("warmup done", flush=True)

    total = len(CONFIGS) * len(QUERY_NUMS)
    i = 0
    t_start = time.time()
    for cfg in CONFIGS:
        ant = None
        if cfg["antagonist_threads"] > 0:
            # Long-lived antagonist covering this whole config block.
            ant = subprocess.Popen(
                [str(ANTAGONIST_BIN), str(cfg["antagonist_threads"]), "100000", "48"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid)
            time.sleep(2)  # let it reach steady state
        try:
            cur.execute(f"set max_parallel_workers_per_gather = {cfg['mpwpg']}")
            cur.execute(f"set work_mem = '{cfg['work_mem_mb']}MB'")
            for n in QUERY_NUMS:
                i += 1
                key_base = (n, cfg["axis"], cfg["mpwpg"], cfg["work_mem_mb"],
                            cfg["antagonist_threads"])
                if all((*key_base, r) in done for r in range(MEASURED_RUNS)):
                    continue
                for _ in range(WARMUP_RUNS):
                    run_once(cur, sqls[n])
                for r in range(MEASURED_RUNS):
                    if (*key_base, r) in done:
                        continue
                    m = run_once(cur, sqls[n])
                    rows.append({
                        "query_num": n, "query_id": f"q{n}",
                        "axis": cfg["axis"], "mpwpg": cfg["mpwpg"],
                        "work_mem_mb": cfg["work_mem_mb"],
                        "antagonist_threads": cfg["antagonist_threads"],
                        "run_index": r, **m,
                        "dop_actual": 1 + m["workers_launched"],
                        "query_reconstruction": "APPROXIMATE" if n in APPROXIMATE else "CONFIRMED",
                        "affinity_control": False,
                        "pg_version": pg_version.split()[1],
                    })
                pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
                el = time.time() - t_start
                print(f"[{i}/{total}] q{n} axis={cfg['axis']} mpwpg={cfg['mpwpg']} "
                      f"wm={cfg['work_mem_mb']}MB ant={cfg['antagonist_threads']} "
                      f"t={rows[-1]['time_ms']:.0f}ms dop={rows[-1]['dop_actual']} "
                      f"({el/60:.1f}min)", flush=True)
        finally:
            if ant is not None:
                os.killpg(os.getpgid(ant.pid), signal.SIGTERM)
                ant.wait()

    pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV} ({len(rows)} measurements, {(time.time()-t_start)/60:.1f} min)")


if __name__ == "__main__":
    sys.exit(main())
