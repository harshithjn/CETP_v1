"""
Collect the arm64 laptop as a DEV-side machine (TASKS.md Tier 1, item 27).

Every dev->prod pair in the dataset is EC2->EC2, but the product claim is "predict
production runtime from a developer's machine". This collects the same workload on an
Apple M1 laptop using the original protocol exactly -- EXPLAIN (ANALYZE, BUFFERS,
FORMAT JSON), server-reported Execution Time -- so laptop->cloud prediction can be
tested for the first time.

21 repetitions rather than 20: repetition 0 is recorded and flagged as a cold-cache
warmup run, since the audit established that repetition 1 of every cell in the original
dataset is a 4-8x outlier that contaminates p95/p99.

Includes q15, which the original collection dropped.

Writes data/laptop/tpch_dataset_m1.csv
"""

import json
import platform
import time
from pathlib import Path

import pandas as pd
import psycopg2

PROJECT_DIR = Path(__file__).resolve().parents[2]
QUERY_DIR = Path(__file__).resolve().parent / "queries"
OUT_DIR = PROJECT_DIR / "data" / "laptop"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_CSV = OUT_DIR / "tpch_dataset_m1.csv"

QUERY_NUMS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]
APPROXIMATE = {16, 20}
REPEATS = 21          # repetition 0 is the flagged warmup
WARMUP_INDEX = 0


def root_buffers(plan, key):
    """Root node only. PostgreSQL reports these cumulatively; summing the tree was the
    bug that inflated the original dataset 3-12x."""
    return plan.get(key, 0) or 0


def main():
    sqls = {n: "\n".join(l for l in (QUERY_DIR / f"q{n}.sql").read_text().splitlines()
                         if not l.strip().startswith("--"))
            for n in QUERY_NUMS}
    conn = psycopg2.connect(dbname="tpch")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("select version()")
    version = cur.fetchone()[0]
    # Factory defaults, matching the original collection environment.
    cur.execute("set max_parallel_workers_per_gather = 2")
    cur.execute("set work_mem = '4MB'")
    print(version)
    print(f"machine: {platform.machine()} / {platform.processor() or 'Apple M1'}")

    rows = []
    t0 = time.time()
    for rep in range(REPEATS):
        for n in QUERY_NUMS:
            cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sqls[n]}")
            res = cur.fetchone()[0][0]
            plan = res["Plan"]
            rows.append({
                "machine_id": "m1", "query_id": f"q{n}", "rep": rep,
                "is_warmup": rep == WARMUP_INDEX,
                "cost": plan.get("Total Cost"),
                "time_ms": res.get("Execution Time"),
                "shared_hit": root_buffers(plan, "Shared Hit Blocks"),
                "shared_read": root_buffers(plan, "Shared Read Blocks"),
                "rows": plan.get("Actual Rows"),
                "workers_launched": plan.get("Workers Launched", 0) or 0,
                "query_reconstruction": "APPROXIMATE" if n in APPROXIMATE else "CONFIRMED",
            })
        pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
        print(f"rep {rep + 1}/{REPEATS} done ({(time.time() - t0) / 60:.1f} min)", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(OUT_CSV, index=False)
    warm = df[~df.is_warmup]
    print(f"\nWrote {OUT_CSV}  ({len(df)} rows, {len(warm)} warm)")
    print(warm.groupby("query_id")["time_ms"].median().round(1).to_string())


if __name__ == "__main__":
    main()
