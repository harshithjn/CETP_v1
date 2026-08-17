import json
import re
from pathlib import Path
import psycopg2

QUERY_DIR = Path("/Users/harshithj/Main/Archive/OtherFiles/patched_queries")
RESULTS_DIR = Path("/Users/harshithj/Main/Resources/CETP/results")
RESULTS_DIR.mkdir(exist_ok=True)

QUERY_NUMS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 21, 22]

PARALLEL_NODE_TYPES = {"Gather", "Gather Merge"}


def strip_comment(sql_text):
    lines = [l for l in sql_text.splitlines() if not l.strip().startswith("--")]
    return "\n".join(lines)


def collect_parallel_info(plan_node, found):
    node_type = plan_node.get("Node Type", "")
    if node_type in PARALLEL_NODE_TYPES:
        found["gather_nodes"].append({
            "node_type": node_type,
            "workers_planned": plan_node.get("Workers Planned"),
            "workers_launched": plan_node.get("Workers Launched"),
        })
    if plan_node.get("Parallel Aware"):
        found["parallel_aware_nodes"].append(node_type)
    for child in plan_node.get("Plans", []):
        collect_parallel_info(child, found)


def run_explain(cur, sql_text):
    cur.execute(f"EXPLAIN (ANALYZE, VERBOSE, FORMAT JSON) {sql_text}")
    result = cur.fetchone()[0]
    return result[0]["Plan"]


conn = psycopg2.connect(dbname="tpch", host="localhost", port=5432)
conn.autocommit = True
cur = conn.cursor()

cur.execute("SHOW max_parallel_workers_per_gather;")
mpwg = cur.fetchone()[0]
cur.execute("SHOW max_parallel_workers;")
mpw = cur.fetchone()[0]
cur.execute("SHOW max_worker_processes;")
mwp = cur.fetchone()[0]

print("=" * 78)
print("PARALLEL-QUERY CONFIGURATION (this local server)")
print("=" * 78)
print(f"max_parallel_workers_per_gather = {mpwg}")
print(f"max_parallel_workers = {mpw}")
print(f"max_worker_processes = {mwp}")
print("(This is PostgreSQL's factory default since PG10, unmodified here. The original")
print(" AWS collection guide never mentions tuning parallel-query settings, so absent")
print(" evidence otherwise, the collection instances almost certainly ran at this same")
print(" default. The 5 collection EC2 instances are already terminated and cannot be")
print(" re-queried directly, so this is an inference from documented default behavior")
print(" plus the guide's silence on the topic, not a re-measurement of the original.)")

rows = []
for qnum in QUERY_NUMS:
    query_id = f"q{qnum}"
    sql_path = QUERY_DIR / f"{query_id}.sql"
    sql_text = strip_comment(sql_path.read_text())

    found = {"gather_nodes": [], "parallel_aware_nodes": []}
    try:
        plan_root = run_explain(cur, sql_text)
        collect_parallel_info(plan_root, found)
        uses_parallel = len(found["gather_nodes"]) > 0
        max_workers_planned = max([g["workers_planned"] or 0 for g in found["gather_nodes"]], default=0)
        max_workers_launched = max([g["workers_launched"] or 0 for g in found["gather_nodes"]], default=0)
        error = None
    except Exception as e:
        conn.rollback()
        uses_parallel = None
        max_workers_planned = None
        max_workers_launched = None
        error = str(e)[:200]

    rows.append({
        "query_id": query_id,
        "query_num": qnum,
        "uses_parallel": uses_parallel,
        "gather_node_count": len(found["gather_nodes"]),
        "workers_planned": max_workers_planned,
        "workers_launched": max_workers_launched,
        "error": error,
    })
    status = "ERROR" if error else ("PARALLEL" if uses_parallel else "serial")
    print(f"{query_id:<6} {status:<10} workers_planned={max_workers_planned} workers_launched={max_workers_launched}" + (f"  ERROR: {error}" if error else ""))

import pandas as pd
df = pd.DataFrame(rows)
df.to_csv(RESULTS_DIR / "addition4b_parallel_plan_check.csv", index=False)
print(f"\nSaved: {RESULTS_DIR / 'addition4b_parallel_plan_check.csv'}")

n_parallel = df["uses_parallel"].sum()
print(f"\n{n_parallel} of {len(df)} queries use parallel execution (Gather/Gather Merge present).")

cur.close()
conn.close()
