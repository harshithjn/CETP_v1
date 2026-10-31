import json, subprocess, sys

JOIN = {"Nested Loop", "Hash Join", "Merge Join"}
SUB = {"SubPlan", "InitPlan"}

def walk(n, d, a):
    a["node_count"] += 1; a["max_depth"] = max(a["max_depth"], d)
    ar = n.get("Actual Rows", 0) or 0; al = n.get("Actual Loops", 1) or 1
    a["rows_processed"] += ar * al
    for c in n.get("Plans", []):
        walk(c, d + 1, a)

def fingerprint(sql):
    r = subprocess.run(["psql","-d","tpch","-t","-A","-c",
                        f"EXPLAIN (ANALYZE, VERBOSE, FORMAT JSON) {sql}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr.strip().splitlines()[0][:80]
    plan = json.loads(r.stdout)[0]["Plan"]
    a = {"node_count":0,"max_depth":0,"rows_processed":0}
    walk(plan, 1, a)
    return {"rows_processed": a["rows_processed"], "node_count": a["node_count"],
            "plan_depth": a["max_depth"], "root_actual_rows": plan.get("Actual Rows"),
            "root_cost": plan.get("Total Cost")}, None

if __name__ == "__main__":
    sql = sys.stdin.read()
    f, err = fingerprint(sql)
    print(json.dumps(f) if f else f"ERR {err}")
