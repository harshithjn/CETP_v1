import json
from pathlib import Path

import pandas as pd
import psycopg2

QUERY_DIR = Path("/Users/harshithj/Main/Archive/OtherFiles/patched_queries")
RESULTS_DIR = Path("/Users/harshithj/Main/Resources/CETP/results")
RESULTS_DIR.mkdir(exist_ok=True)

QUERY_NUMS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 21, 22]

JOIN_NODE_TYPES = {"Nested Loop", "Hash Join", "Merge Join"}
SUBPLAN_RELATIONSHIPS = {"SubPlan", "InitPlan"}


def strip_comment(sql_text):
    lines = [l for l in sql_text.splitlines() if not l.strip().startswith("--")]
    return "\n".join(lines)


def walk(node, depth, acc):
    acc["node_count"] += 1
    acc["max_depth"] = max(acc["max_depth"], depth)

    actual_rows = node.get("Actual Rows", 0) or 0
    actual_loops = node.get("Actual Loops", 1) or 1
    acc["rows_processed"] += actual_rows * actual_loops

    node_type = node.get("Node Type", "")
    if node_type in JOIN_NODE_TYPES:
        acc["join_count"] += 1
    if node_type == "Aggregate":
        acc["agg_count"] += 1
    if node_type == "Sort":
        acc["sort_count"] += 1

    parent_rel = node.get("Parent Relationship", "")
    if parent_rel in SUBPLAN_RELATIONSHIPS or node_type == "SubPlan":
        acc["subplan_count"] += 1
    if actual_loops > 1:
        acc["rescan_count"] += 1

    for child in node.get("Plans", []):
        walk(child, depth + 1, acc)


def extract_features(cur, sql_text):
    cur.execute(f"EXPLAIN (ANALYZE, VERBOSE, FORMAT JSON) {sql_text}")
    result = cur.fetchone()[0]
    plan_root = result[0]["Plan"]
    exec_time = result[0].get("Execution Time")

    acc = {
        "node_count": 0, "max_depth": 0, "rows_processed": 0,
        "join_count": 0, "agg_count": 0, "sort_count": 0,
        "subplan_count": 0, "rescan_count": 0,
    }
    walk(plan_root, 1, acc)

    return {
        "root_cost": plan_root.get("Total Cost"),
        "root_actual_rows": plan_root.get("Actual Rows"),
        "explain_exec_time_ms": exec_time,
        "node_count": acc["node_count"],
        "plan_depth": acc["max_depth"],
        "rows_processed": acc["rows_processed"],
        "join_count": acc["join_count"],
        "agg_count": acc["agg_count"],
        "has_aggregate": int(acc["agg_count"] > 0),
        "sort_count": acc["sort_count"],
        "has_sort": int(acc["sort_count"] > 0),
        "subplan_count": acc["subplan_count"],
        "rescan_count": acc["rescan_count"],
        "correlated_pattern_count": acc["subplan_count"] + acc["rescan_count"],
        "has_correlated_pattern": int((acc["subplan_count"] + acc["rescan_count"]) > 0),
    }


def main():
    conn = psycopg2.connect(dbname="tpch", host="localhost", port=5432)
    conn.autocommit = True
    cur = conn.cursor()

    rows = []
    for qnum in QUERY_NUMS:
        query_id = f"q{qnum}"
        sql_text = strip_comment((QUERY_DIR / f"{query_id}.sql").read_text())
        feats = extract_features(cur, sql_text)
        feats["query_id"] = query_id
        feats["query_num"] = qnum
        rows.append(feats)
        print(f"{query_id:<6} rows_processed={feats['rows_processed']:>10}  "
              f"root_actual_rows={feats['root_actual_rows']:>8}  "
              f"nodes={feats['node_count']:>3}  depth={feats['plan_depth']:>2}  "
              f"joins={feats['join_count']}  agg={feats['has_aggregate']}  "
              f"sort={feats['has_sort']}  correlated={feats['correlated_pattern_count']}")

    cur.close()
    conn.close()

    df = pd.DataFrame(rows)
    cols = ["query_id", "query_num", "root_cost", "root_actual_rows", "explain_exec_time_ms",
            "rows_processed", "node_count", "plan_depth", "join_count",
            "agg_count", "has_aggregate", "sort_count", "has_sort",
            "subplan_count", "rescan_count", "correlated_pattern_count", "has_correlated_pattern"]
    df = df[cols].sort_values("query_num")
    out_path = RESULTS_DIR / "plan_structure_features.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
