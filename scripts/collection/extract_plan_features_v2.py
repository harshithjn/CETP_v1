"""
Per-node-type plan features (TASKS.md Tier 1, item 25).

The dev-side workload descriptor is currently six scalars read off the root node
(cost, shared_hit, shared_read, total_buffers, io_ratio, rows). That discards the whole
plan tree: which operators run, how much each costs, how wide the joins are, whether
anything spills.

This emits ~40 features instead: global shape, plus per-node-type aggregates for the
operator families that matter in TPC-H. Evidence it is worth doing already exists --
structure-only features scored 0.454 macro-F1 on the bottleneck classifier against 0.375
for the buffer features (results/fix2_classifier_structural_summary.json).

Run against the local SF1 instance; writes results/plan_features_v2.csv.
"""

import json
from pathlib import Path

import pandas as pd
import psycopg2

PROJECT_DIR = Path(__file__).resolve().parents[2]
QUERY_DIR = Path(__file__).resolve().parent / "queries"
RESULTS_DIR = PROJECT_DIR / "results"

# Operator families, not individual node types: individual types are too sparse at 22
# queries, and these are the groupings that correspond to distinct resource demands.
FAMILIES = {
    "seqscan": {"Seq Scan"},
    "idxscan": {"Index Scan", "Index Only Scan", "Bitmap Index Scan", "Bitmap Heap Scan"},
    "hashjoin": {"Hash Join"},
    "mergejoin": {"Merge Join"},
    "nestloop": {"Nested Loop"},
    "hash": {"Hash"},
    "agg": {"Aggregate"},
    "sort": {"Sort", "Incremental Sort"},
    "gather": {"Gather", "Gather Merge"},
    "materialize": {"Materialize", "Memoize"},
}
# Queries whose reconstruction is only APPROXIMATE -- see queries/README.md
APPROXIMATE = {16, 20}
QUERY_NUMS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]


def family_of(node_type):
    for fam, types in FAMILIES.items():
        if node_type in types:
            return fam
    return "other"


def walk(node, depth, acc):
    nt = node.get("Node Type", "")
    fam = family_of(nt)
    rows = (node.get("Actual Rows", 0) or 0) * (node.get("Actual Loops", 1) or 1)
    acc["node_count"] += 1
    acc["max_depth"] = max(acc["max_depth"], depth)
    acc[f"n_{fam}"] = acc.get(f"n_{fam}", 0) + 1
    acc[f"rows_{fam}"] = acc.get(f"rows_{fam}", 0) + rows
    acc[f"cost_{fam}"] = acc.get(f"cost_{fam}", 0.0) + (node.get("Total Cost", 0.0) or 0.0)
    acc["temp_read"] += node.get("Temp Read Blocks", 0) or 0
    acc["temp_written"] += node.get("Temp Written Blocks", 0) or 0
    acc["workers_launched"] = max(acc["workers_launched"], node.get("Workers Launched", 0) or 0)
    if node.get("Parallel Aware"):
        acc["n_parallel_aware"] += 1
    if node.get("Parent Relationship") in {"SubPlan", "InitPlan"}:
        acc["n_subplan"] += 1
    if (node.get("Actual Loops", 1) or 1) > 1:
        acc["n_rescan"] += 1
    # Cardinality misestimation: the single strongest known predictor of plan quality.
    est, act = node.get("Plan Rows", 0) or 0, node.get("Actual Rows", 0) or 0
    if est > 0 and act > 0:
        acc["qerror_sum"] += max(est / act, act / est)
        acc["qerror_n"] += 1
    for c in node.get("Plans", []):
        walk(c, depth + 1, acc)


def extract(cur, sql):
    cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON) {sql}")
    res = cur.fetchone()[0][0]
    root = res["Plan"]
    acc = {"node_count": 0, "max_depth": 0, "temp_read": 0, "temp_written": 0,
           "workers_launched": 0, "n_parallel_aware": 0, "n_subplan": 0, "n_rescan": 0,
           "qerror_sum": 0.0, "qerror_n": 0}
    walk(root, 1, acc)
    total_cost = root.get("Total Cost", 0.0) or 1.0
    out = {
        "root_cost": root.get("Total Cost"), "root_rows": root.get("Actual Rows"),
        "exec_time_ms": res.get("Execution Time"), "planning_ms": res.get("Planning Time"),
        "shared_hit": root.get("Shared Hit Blocks", 0) or 0,
        "shared_read": root.get("Shared Read Blocks", 0) or 0,
        "node_count": acc["node_count"], "plan_depth": acc["max_depth"],
        "temp_read": acc["temp_read"], "temp_written": acc["temp_written"],
        "spills": int(acc["temp_written"] > 0),
        "workers_launched": acc["workers_launched"],
        "dop": 1 + acc["workers_launched"],
        "n_parallel_aware": acc["n_parallel_aware"],
        "n_subplan": acc["n_subplan"], "n_rescan": acc["n_rescan"],
        "mean_card_qerror": (acc["qerror_sum"] / acc["qerror_n"]) if acc["qerror_n"] else 1.0,
    }
    for fam in list(FAMILIES) + ["other"]:
        out[f"n_{fam}"] = acc.get(f"n_{fam}", 0)
        out[f"rows_{fam}"] = acc.get(f"rows_{fam}", 0)
        # Cost share, not raw cost: scale-free, so it transfers across scale factors.
        out[f"costshare_{fam}"] = acc.get(f"cost_{fam}", 0.0) / total_cost
    return out


def main():
    conn = psycopg2.connect(dbname="tpch")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("set max_parallel_workers_per_gather = 2")
    cur.execute("set work_mem = '4MB'")
    rows = []
    for n in QUERY_NUMS:
        sql = "\n".join(l for l in (QUERY_DIR / f"q{n}.sql").read_text().splitlines()
                        if not l.strip().startswith("--"))
        extract(cur, sql)              # warmup: repetition 1 is a cold-cache outlier
        f = extract(cur, sql)
        rows.append({"query_id": f"q{n}", "query_num": n,
                     "query_reconstruction": "APPROXIMATE" if n in APPROXIMATE else "CONFIRMED",
                     **f})
        print(f"q{n:<3} nodes={f['node_count']:>3} depth={f['plan_depth']:>2} "
              f"dop={f['dop']} spills={f['spills']} cardqerr={f['mean_card_qerror']:.1f} "
              f"t={f['exec_time_ms']:.0f}ms", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "plan_features_v2.csv", index=False)
    print(f"\nWrote {RESULTS_DIR / 'plan_features_v2.csv'}  "
          f"({len(df)} queries x {len(df.columns)-3} features)")


if __name__ == "__main__":
    main()
