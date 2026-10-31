"""
Unified collection harness (TASKS.md Tier 2, items 30, 35, 36, 37).

One collector for every workload, concurrency level, Postgres version and machine. It
replaces collect_query.py and collect_laptop.py, both of which hard-coded a single
workload and a single configuration.

What it fixes relative to the original collection:

  item 37  Stores the RAW plan JSON, not just six derived scalars. The original kept only
           derived features, so when the buffer-counting bug was found the plans could not
           be re-examined and the dataset had to be repaired from a local re-run. To avoid
           250 MB of near-identical duplicates it stores the full plan for the first
           measured repetition of each cell, then a structural hash for every repetition,
           and stores any plan whose hash differs. That captures PLAN DRIFT across
           repetitions and machines, which is the thing six scalars can never show.

  item 35  --concurrency N runs N clients against the same query simultaneously, so
           runtime under contention is measured rather than assumed. Every previous
           measurement in this project was single-query-at-a-time.

  item 36  --pg-version / --port target any installed Postgres cluster; the server version
           and every planner-relevant setting are recorded per row, so a version matrix is
           just repeated invocations.

  warmup   Repetition 0 is recorded and flagged. The audit established that repetition 1 of
           every cell in the original dataset is a 4-8x cold-cache outlier which silently
           contaminated every p95 and p99.

Usage:
  python3 scripts/collection/collect.py \
      --workload tpch --queries scripts/collection/queries --db tpch \
      --machine-id m1 --reps 21 --concurrency 1 \
      --out data/laptop/tpch_m1_c1.csv --plans-dir data/plans
"""

import argparse
import gzip
import hashlib
import json
import platform
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import psycopg2

PROJECT_DIR = Path(__file__).resolve().parents[2]

# Recorded per row so a result can never be attributed to the wrong configuration.
TRACKED_SETTINGS = [
    "server_version", "max_parallel_workers_per_gather", "max_parallel_workers",
    "max_worker_processes", "shared_buffers", "effective_cache_size", "work_mem",
    "random_page_cost", "seq_page_cost", "jit",
]


def plan_signature(node):
    """Structural fingerprint: node types, join order and parallelism, ignoring timings.
    Two plans with the same signature are the same plan shape."""
    parts = [node.get("Node Type", ""),
             str(node.get("Parallel Aware", False)),
             str(node.get("Workers Planned", 0) or 0),
             node.get("Relation Name", "") or "",
             node.get("Join Type", "") or "",
             node.get("Index Name", "") or ""]
    for c in node.get("Plans", []):
        parts.append(plan_signature(c))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def root_buffers(plan, key):
    """Root node only. PostgreSQL reports these cumulatively; summing the tree was the
    bug that inflated the original dataset 3-12x."""
    return plan.get(key, 0) or 0


def walk(node, acc, depth=1):
    acc["node_count"] += 1
    acc["max_depth"] = max(acc["max_depth"], depth)
    acc["workers_launched"] = max(acc["workers_launched"], node.get("Workers Launched", 0) or 0)
    acc["temp_written"] += node.get("Temp Written Blocks", 0) or 0
    acc["rows_processed"] += ((node.get("Actual Rows", 0) or 0)
                             * (node.get("Actual Loops", 1) or 1))
    for c in node.get("Plans", []):
        walk(c, acc, depth + 1)


def machine_facts():
    """Identity recorded with every row. On EC2 this should be IMDSv2-confirmed; locally
    it falls back to platform data, and says which it used."""
    facts = {"hostname": socket.gethostname(), "arch": platform.machine(),
             "platform": platform.platform(), "identity_source": "platform"}
    try:
        tok = subprocess.run(
            ["curl", "-sX", "PUT", "http://169.254.169.254/latest/api/token",
             "-H", "X-aws-ec2-metadata-token-ttl-seconds: 60"],
            capture_output=True, text=True, timeout=2).stdout.strip()
        if tok:
            out = {}
            for k in ("instance-type", "instance-id"):
                out[k] = subprocess.run(
                    ["curl", "-s", "-H", f"X-aws-ec2-metadata-token: {tok}",
                     f"http://169.254.169.254/latest/meta-data/{k}"],
                    capture_output=True, text=True, timeout=2).stdout.strip()
            if out.get("instance-type"):
                facts.update({"instance_type": out["instance-type"],
                              "instance_id": out["instance-id"],
                              "identity_source": "imdsv2"})
    except (subprocess.TimeoutExpired, OSError):
        pass
    return facts


def connect(args):
    conn = psycopg2.connect(dbname=args.db, port=args.port,
                            host=args.host) if args.host else \
        psycopg2.connect(dbname=args.db, port=args.port)
    conn.autocommit = True
    return conn


def settings_of(cur):
    out = {}
    for s in TRACKED_SETTINGS:
        try:
            cur.execute(f"show {s}")
            out[s] = cur.fetchone()[0]
        except psycopg2.Error:
            out[s] = None
    return out


def run_one(args, sql, timeout_ms):
    """One EXPLAIN ANALYZE on its own connection, so concurrent clients are independent."""
    conn = connect(args)
    cur = conn.cursor()
    try:
        cur.execute(f"set statement_timeout = {timeout_ms}")
        cur.execute(f"set max_parallel_workers_per_gather = {args.mpwpg}")
        cur.execute(f"set work_mem = '{args.work_mem}'")
        t0 = time.time()
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
        res = cur.fetchone()[0][0]
        return {"ok": True, "plan": res, "wall_ms": (time.time() - t0) * 1000}
    except psycopg2.Error as e:
        return {"ok": False, "error": str(e).strip().splitlines()[0][:200],
                "wall_ms": (time.time() - t0) * 1000 if "t0" in dir() else None}
    finally:
        cur.close()
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workload", required=True, choices=["tpch", "tpcds", "job"])
    ap.add_argument("--queries", required=True, help="directory of .sql files")
    ap.add_argument("--db", required=True)
    ap.add_argument("--machine-id", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plans-dir", default=None, help="raw plan JSON store (item 37)")
    ap.add_argument("--reps", type=int, default=21, help="repetition 0 is the warmup")
    ap.add_argument("--concurrency", type=int, default=1, help="simultaneous clients")
    ap.add_argument("--scale-factor", default="SF1")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--host", default=None)
    ap.add_argument("--mpwpg", type=int, default=2)
    ap.add_argument("--work-mem", default="4MB")
    ap.add_argument("--timeout-ms", type=int, default=300000)
    ap.add_argument("--only", default=None, help="comma-separated query ids to restrict to")
    args = ap.parse_args()

    qdir = Path(args.queries)
    files = sorted(qdir.glob("*.sql"), key=lambda p: (len(p.stem), p.stem))
    if args.only:
        keep = set(args.only.split(","))
        files = [f for f in files if f.stem in keep]
    if not files:
        sys.exit(f"no .sql files in {qdir}")

    sqls = {}
    for f in files:
        txt = "\n".join(l for l in f.read_text().splitlines()
                        if not l.strip().startswith("--")).strip()
        if txt:
            sqls[f.stem] = txt

    facts = machine_facts()
    conn = connect(args)
    cur = conn.cursor()
    setts = settings_of(cur)
    cur.close()
    conn.close()
    print(f"machine={args.machine_id} ({facts['arch']}, identity={facts['identity_source']}) "
          f"pg={setts['server_version']} workload={args.workload} "
          f"queries={len(sqls)} reps={args.reps} concurrency={args.concurrency}")

    plans_dir = Path(args.plans_dir) if args.plans_dir else None
    if plans_dir:
        plans_dir = plans_dir / args.machine_id / args.workload / f"c{args.concurrency}"
        plans_dir.mkdir(parents=True, exist_ok=True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows, seen_sig = [], {}
    t_start = time.time()

    for rep in range(args.reps):
        for qid, sql in sqls.items():
            if args.concurrency == 1:
                results = [run_one(args, sql, args.timeout_ms)]
            else:
                with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
                    results = list(ex.map(
                        lambda _: run_one(args, sql, args.timeout_ms),
                        range(args.concurrency)))

            for client, r in enumerate(results):
                base = {
                    "machine_id": args.machine_id, "workload": args.workload,
                    "scale_factor": args.scale_factor, "query_id": qid,
                    "rep": rep, "is_warmup": rep == 0, "client": client,
                    "concurrency": args.concurrency,
                    "arch": facts["arch"], "identity_source": facts["identity_source"],
                    "instance_type": facts.get("instance_type"),
                    **{f"pg_{k}": v for k, v in setts.items()},
                    "mpwpg_set": args.mpwpg, "work_mem_set": args.work_mem,
                }
                if not r["ok"]:
                    rows.append({**base, "ok": False, "error": r["error"],
                                 "time_ms": None, "wall_ms": r["wall_ms"]})
                    continue
                res = r["plan"]
                plan = res["Plan"]
                acc = {"node_count": 0, "max_depth": 0, "workers_launched": 0,
                       "temp_written": 0, "rows_processed": 0}
                walk(plan, acc)
                sig = plan_signature(plan)
                key = (qid, args.concurrency)

                # item 37: store the plan the first time, and again whenever the SHAPE
                # changes -- that is plan drift, and it is invisible in derived scalars.
                stored = None
                if plans_dir is not None and seen_sig.get(key) != sig:
                    stored = f"{qid}_r{rep}_c{client}_{sig}.json.gz"
                    with gzip.open(plans_dir / stored, "wt") as fh:
                        json.dump(res, fh)
                    if key not in seen_sig:
                        seen_sig[key] = sig
                    else:
                        print(f"    PLAN DRIFT on {qid}: {seen_sig[key]} -> {sig}")
                        seen_sig[key] = sig

                rows.append({
                    **base, "ok": True, "error": None,
                    "time_ms": res.get("Execution Time"),
                    "planning_ms": res.get("Planning Time"),
                    "wall_ms": r["wall_ms"],
                    "cost": plan.get("Total Cost"),
                    "rows": plan.get("Actual Rows"),
                    "shared_hit": root_buffers(plan, "Shared Hit Blocks"),
                    "shared_read": root_buffers(plan, "Shared Read Blocks"),
                    "temp_written": acc["temp_written"],
                    "rows_processed": acc["rows_processed"],
                    "node_count": acc["node_count"], "plan_depth": acc["max_depth"],
                    "workers_launched": acc["workers_launched"],
                    "dop": 1 + acc["workers_launched"],
                    "plan_signature": sig, "plan_json_file": stored,
                })
        pd.DataFrame(rows).to_csv(out_path, index=False)
        ok = sum(1 for r in rows if r.get("ok"))
        print(f"rep {rep + 1}/{args.reps} done ({(time.time() - t_start) / 60:.1f} min, "
              f"{ok}/{len(rows)} ok)", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)
    nfail = int((~df["ok"]).sum())
    print(f"\nWrote {out_path}  ({len(df)} rows, {nfail} failed)")
    if nfail:
        bad = df[~df["ok"]].groupby("query_id")["error"].first()
        print(f"failing queries ({len(bad)}):")
        for q, e in bad.items():
            print(f"  {q}: {e}")
    print(f"distinct plan shapes: {df['plan_signature'].nunique()} across "
          f"{df['query_id'].nunique()} queries")


if __name__ == "__main__":
    main()
