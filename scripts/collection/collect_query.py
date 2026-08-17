import json
import subprocess
import sys
import time
from pathlib import Path

DB_NAME = sys.argv[1] if len(sys.argv) > 1 else "tpch"
QUERY_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/Users/harshithj/Main/Archive/OtherFiles/patched_queries")
OUT_DIR = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("/tmp/collected")
REPEATS = int(sys.argv[4]) if len(sys.argv) > 4 else 20
TIMEOUT = int(sys.argv[5]) if len(sys.argv) > 5 else 600

_ALL_QUERY_NUMS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 21, 22]
QUERY_NUMS = (
    [int(x) for x in sys.argv[6].split(",")] if len(sys.argv) > 6 else _ALL_QUERY_NUMS
)

OUT_DIR.mkdir(parents=True, exist_ok=True)


def strip_comment(sql_text):
    lines = [l for l in sql_text.splitlines() if not l.strip().startswith("--")]
    return "\n".join(lines)


def root_buffers(plan_node, key):
    return plan_node.get(key, 0) or 0


def run_once(sql_text):
    cmd = [
        "sudo", "-u", "postgres",
        "taskset", "-c", "1",
        "psql", "-d", DB_NAME, "-t", "-A", "-c",
        f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql_text}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[:500])
    parsed = json.loads(result.stdout)
    plan = parsed[0]["Plan"]
    return {
        "cost": plan.get("Total Cost"),
        "time_ms": parsed[0].get("Execution Time"),
        "shared_hit": root_buffers(plan, "Shared Hit Blocks"),
        "shared_read": root_buffers(plan, "Shared Read Blocks"),
        "rows": plan.get("Actual Rows"),
    }


def main():
    for qnum in QUERY_NUMS:
        query_id = f"q{qnum}"
        sql_path = QUERY_DIR / f"{query_id}.sql"
        sql_text = strip_comment(sql_path.read_text())
        out_path = OUT_DIR / f"{query_id}_runs.jsonl"

        records = []
        error = None
        for i in range(REPEATS):
            try:
                rec = run_once(sql_text)
                records.append(rec)
            except Exception as e:
                error = str(e)
                break

        with open(out_path, "w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        status = "ERROR" if error else ("OK" if len(records) == REPEATS else "INCOMPLETE")
        print(f"{query_id} {status} n={len(records)}" + (f" error={error}" if error else ""))


if __name__ == "__main__":
    main()
