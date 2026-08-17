import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

QUERY_DIR = Path("/Users/harshithj/Main/Archive/OtherFiles/patched_queries")
RESULTS_DIR = Path("/Users/harshithj/Main/Resources/CETP/results")
RESULTS_DIR.mkdir(exist_ok=True)

QUERY_NUMS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 21, 22]
CONFIGS = {"OFF": 0, "ON": 2}
REPEATS = 5


def strip_comment(sql_text):
    lines = [l for l in sql_text.splitlines() if not l.strip().startswith("--")]
    return "\n".join(lines)


def run_once(cur, sql_text):
    cur.execute(f"EXPLAIN (ANALYZE, FORMAT JSON) {sql_text}")
    result = cur.fetchone()[0]
    return result[0]["Execution Time"]


def main():
    conn = psycopg2.connect(dbname="tpch", host="localhost", port=5432)
    conn.autocommit = True
    cur = conn.cursor()

    rows = []
    t_start = time.time()
    for qnum in QUERY_NUMS:
        query_id = f"q{qnum}"
        sql_text = strip_comment((QUERY_DIR / f"{query_id}.sql").read_text())
        for config_name, workers in CONFIGS.items():
            cur.execute(f"SET max_parallel_workers_per_gather = {workers};")
            run_once(cur, sql_text)  # untimed warmup
            times = [run_once(cur, sql_text) for _ in range(REPEATS)]
            median_time = float(np.median(times))
            rows.append({
                "query_id": query_id, "query_num": qnum, "config": config_name,
                "max_parallel_workers_per_gather": workers,
                "times_ms": json.dumps(times), "median_time_ms": median_time,
            })
            print(f"{query_id:<6} {config_name:<4} median={median_time:>9.2f}ms  raw={['%.1f' % t for t in times]}")

    cur.close()
    conn.close()

    df = pd.DataFrame(rows)
    out_path = RESULTS_DIR / "fix3_parallel_timing.csv"
    df.to_csv(out_path, index=False)
    print(f"\nTotal wall time: {time.time() - t_start:.1f}s")
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
