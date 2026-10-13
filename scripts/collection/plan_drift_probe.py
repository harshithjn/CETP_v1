"""
N1: plan drift (TASKS.md Tier 3, item 39) -- measurement.

The project's model assumes workload cost is hardware-invariant and only machine
capability changes. That assumption is false for a relational optimizer: PostgreSQL's
planner reads hardware-derived settings (max_parallel_workers_per_gather,
effective_cache_size, work_mem, random_page_cost) and picks a DIFFERENT PLAN on different
hardware. The dev-side cost vector therefore describes a plan production will not run.

A literature check (RELATED_WORK.md) found no formal treatment of this: the mechanism is
discussed only in vendor blogs, and no paper decomposes cross-environment error into plan
divergence versus capability scaling.

### Why this runs on one machine, and why that is BETTER than two

On a single machine hardware capability is CONSTANT. Varying only the planner's hardware
*beliefs* therefore isolates plan divergence with no capability confound at all -- which a
two-machine experiment cannot do, since there both terms move together. Each environment
below is a plausible machine profile the planner would infer.

Settings that affect execution as well as planning (work_mem, mpwpg) are confounded within
an environment, so the analysis separates pairs of environments that produced the SAME plan
signature (pure execution effect) from pairs that produced DIFFERENT signatures (plan +
execution). See tier3_plan_drift.py.

Writes results/plan_drift_raw.csv.
"""

import argparse
import hashlib
import sys
import time
from pathlib import Path

import pandas as pd
import psycopg2

PROJECT_DIR = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# Each environment is a machine profile expressed purely as planner beliefs.
ENVIRONMENTS = {
    "small": {"max_parallel_workers_per_gather": 0, "effective_cache_size": "256MB",
              "work_mem": "1MB", "random_page_cost": 4.0},
    "mid": {"max_parallel_workers_per_gather": 2, "effective_cache_size": "4GB",
            "work_mem": "4MB", "random_page_cost": 4.0},
    "big": {"max_parallel_workers_per_gather": 8, "effective_cache_size": "16GB",
            "work_mem": "64MB", "random_page_cost": 1.1},
    # Single-axis variants, so each setting's contribution to drift is attributable.
    "cores_only": {"max_parallel_workers_per_gather": 8, "effective_cache_size": "4GB",
                   "work_mem": "4MB", "random_page_cost": 4.0},
    "nvme_only": {"max_parallel_workers_per_gather": 2, "effective_cache_size": "4GB",
                  "work_mem": "4MB", "random_page_cost": 1.1},
    "memory_only": {"max_parallel_workers_per_gather": 2, "effective_cache_size": "4GB",
                    "work_mem": "64MB", "random_page_cost": 4.0},
}

WARMUP = 1
MEASURED = 2


def plan_signature(node):
    """Structural fingerprint: operators, join order, parallelism. Ignores all timings."""
    parts = [node.get("Node Type", ""), str(node.get("Parallel Aware", False)),
             str(node.get("Workers Planned", 0) or 0),
             node.get("Relation Name", "") or "", node.get("Join Type", "") or "",
             node.get("Index Name", "") or "", node.get("Strategy", "") or ""]
    for c in node.get("Plans", []):
        parts.append(plan_signature(c))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def walk(node, acc, depth=1):
    acc["node_count"] += 1
    acc["max_depth"] = max(acc["max_depth"], depth)
    acc["workers_launched"] = max(acc["workers_launched"], node.get("Workers Launched", 0) or 0)
    acc["temp_written"] += node.get("Temp Written Blocks", 0) or 0
    nt = node.get("Node Type", "")
    if nt in ("Hash Join", "Merge Join", "Nested Loop"):
        acc["joins"] += 1
        acc["join_types"].append(nt)
    if nt in ("Seq Scan",):
        acc["seqscans"] += 1
    if nt in ("Index Scan", "Index Only Scan", "Bitmap Heap Scan"):
        acc["idxscans"] += 1
    for c in node.get("Plans", []):
        walk(c, acc, depth + 1)


def run(cur, sql):
    cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
    res = cur.fetchone()[0][0]
    plan = res["Plan"]
    acc = {"node_count": 0, "max_depth": 0, "workers_launched": 0, "temp_written": 0,
           "joins": 0, "seqscans": 0, "idxscans": 0, "join_types": []}
    walk(plan, acc)
    return {"time_ms": res["Execution Time"], "planning_ms": res.get("Planning Time"),
            "cost": plan.get("Total Cost"), "rows": plan.get("Actual Rows"),
            "signature": plan_signature(plan), "node_count": acc["node_count"],
            "plan_depth": acc["max_depth"], "dop": 1 + acc["workers_launched"],
            "temp_written": acc["temp_written"], "joins": acc["joins"],
            "seqscans": acc["seqscans"], "idxscans": acc["idxscans"],
            "join_types": ",".join(acc["join_types"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="tpch")
    ap.add_argument("--queries", default="scripts/collection/queries")
    ap.add_argument("--workload", default="tpch")
    ap.add_argument("--out", default=str(RESULTS_DIR / "plan_drift_raw.csv"))
    ap.add_argument("--timeout-ms", type=int, default=180000)
    args = ap.parse_args()

    qdir = PROJECT_DIR / args.queries if not Path(args.queries).is_absolute() else Path(args.queries)
    files = sorted(qdir.glob("*.sql"), key=lambda p: (len(p.stem), p.stem))
    sqls = {}
    for f in files:
        t = "\n".join(l for l in f.read_text().splitlines()
                      if not l.strip().startswith("--")).strip()
        if t:
            sqls[f.stem] = t
    print(f"{len(sqls)} queries x {len(ENVIRONMENTS)} environments")

    conn = psycopg2.connect(dbname=args.db)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"set statement_timeout = {args.timeout_ms}")

    rows, t0 = [], time.time()
    # Warm the OS page cache once before any measurement.
    for name, env in ENVIRONMENTS.items():
        for k, v in env.items():
            cur.execute(f"set {k} = '{v}'")
        for qid, sql in sqls.items():
            try:
                for _ in range(WARMUP):
                    run(cur, sql)
                for rep in range(MEASURED):
                    r = run(cur, sql)
                    rows.append({"workload": args.workload, "query_id": qid,
                                 "env": name, "rep": rep, "ok": True, "error": None,
                                 **{f"set_{k}": v for k, v in env.items()}, **r})
            except psycopg2.Error as e:
                conn.rollback() if conn.closed == 0 else None
                rows.append({"workload": args.workload, "query_id": qid, "env": name,
                             "rep": 0, "ok": False,
                             "error": str(e).strip().splitlines()[0][:150],
                             **{f"set_{k}": v for k, v in env.items()}})
                cur = conn.cursor()
                cur.execute(f"set statement_timeout = {args.timeout_ms}")
        pd.DataFrame(rows).to_csv(args.out, index=False)
        print(f"  env={name} done ({(time.time() - t0) / 60:.1f} min)", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(args.out, index=False)
    good = df[df["ok"]]
    per_q = good.groupby("query_id")["signature"].nunique()
    print(f"\nWrote {args.out} ({len(df)} rows, {int((~df['ok']).sum())} failed)")
    print(f"queries whose PLAN CHANGED across environments: "
          f"{int((per_q > 1).sum())}/{len(per_q)}")
    print(f"distinct plans per query: min {per_q.min()}, median {per_q.median():.0f}, "
          f"max {per_q.max()}")


if __name__ == "__main__":
    main()
