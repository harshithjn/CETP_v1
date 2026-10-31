"""
Causal plan-drift decomposition, corrected (CONFERENCE_PLAN.md item 3, second attempt).

v1 failed for two identified reasons (results/tier4_plan_drift_causal.md):

  1. Forcing verified on 100% of cells where the plan was already identical and **0%** where
     it differed. Cause: `work_mem` changes the AGGREGATE STRATEGY (Hashed vs Sorted), and
     pg_hint_plan has no hint for it -- a Sorted aggregate also needs a Sort node that a
     Hashed one does not, so the plan tree differs structurally and no hint can bridge it.
  2. `T(B,P_B)` and `T(B,P_A)` were measured in separate sweeps, so buffer-cache state
     drifted between them. q20 recorded 13,304 ms vs 539 ms for the SAME plan -- a 24x
     artifact that swamped any real effect.

Both are fixed here:

  1. **`work_mem` is held constant** at 4MB across every environment. Only beliefs that
     steer scan method, join method, join order and parallel degree are varied -- exactly
     the things pg_hint_plan can force. This narrows the question (it says nothing about
     memory-driven plan change) but makes it answerable.
  2. **Paired runs are interleaved in one session**: native-B and forced-A alternate
     back to back, repeated, and medians are taken per arm. Cache state is therefore shared
     between the two arms rather than differing by a whole sweep.

Writes results/plan_drift_causal_v2_raw.csv.
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan_to_hints import (  # noqa: E402
    enable_hints, force_and_verify, plan_signature, plan_to_hints, run_explain)

PROJECT_DIR = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_DIR / "results"

# work_mem and effective_cache_size are FIXED. Only parallelism and storage beliefs vary.
BASE = {"work_mem": "4MB", "effective_cache_size": "4GB"}
ENVIRONMENTS = {
    "mid":         {**BASE, "max_parallel_workers_per_gather": 2, "random_page_cost": 4.0},
    "cores":       {**BASE, "max_parallel_workers_per_gather": 8, "random_page_cost": 4.0},
    "nvme":        {**BASE, "max_parallel_workers_per_gather": 2, "random_page_cost": 1.1},
    "cores_nvme":  {**BASE, "max_parallel_workers_per_gather": 8, "random_page_cost": 1.1},
    "serial":      {**BASE, "max_parallel_workers_per_gather": 0, "random_page_cost": 4.0},
}
PAIRS = [("mid", "cores"), ("mid", "nvme"), ("mid", "cores_nvme"),
         ("mid", "serial"), ("serial", "cores")]
ROUNDS = 3  # interleaved rounds; median per arm


def apply(cur, env):
    for k, v in env.items():
        cur.execute(f"set {k} = '{v}'")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="tpch")
    ap.add_argument("--queries", default="scripts/collection/queries")
    ap.add_argument("--out", default=str(RESULTS_DIR / "plan_drift_causal_v2_raw.csv"))
    ap.add_argument("--timeout-ms", type=int, default=240000)
    args = ap.parse_args()

    qdir = PROJECT_DIR / args.queries
    sqls = {}
    for f in sorted(qdir.glob("*.sql"), key=lambda p: (len(p.stem), p.stem)):
        t = "\n".join(l for l in f.read_text().splitlines()
                      if not l.strip().startswith("--")).strip()
        if t:
            sqls[f.stem] = t

    conn = psycopg2.connect(dbname=args.db)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"set statement_timeout = {args.timeout_ms}")
    cur.execute("create extension if not exists pg_hint_plan")
    enable_hints(cur)

    rows, t0 = [], time.time()
    for a, b in PAIRS:
        for qid, sql in sqls.items():
            try:
                # Plans in each environment, and a shared warmup.
                apply(cur, ENVIRONMENTS[a])
                run_explain(cur, sql)
                res_a = run_explain(cur, sql)
                sig_a = plan_signature(res_a["Plan"])
                apply(cur, ENVIRONMENTS[b])
                run_explain(cur, sql)
                res_b = run_explain(cur, sql)
                sig_b = plan_signature(res_b["Plan"])

                # INTERLEAVED arms: alternate native-B and forced-A so both see the same
                # cache state. This is the fix for v1's 24x cross-phase artifact.
                t_b, t_bf, verified = [], [], True
                for _ in range(ROUNDS):
                    apply(cur, ENVIRONMENTS[b])
                    t_b.append(run_explain(cur, sql)["Execution Time"])
                    rf, _h, ok = force_and_verify(cur, sql, res_a["Plan"],
                                                  ENVIRONMENTS[b])
                    if not ok or rf is None:
                        verified = False
                        break
                    t_bf.append(rf["Execution Time"])
                # T_A measured last, under A, as the reference arm.
                apply(cur, ENVIRONMENTS[a])
                t_a = sorted(run_explain(cur, sql)["Execution Time"] for _ in range(ROUNDS))
                ta = t_a[len(t_a) // 2]

                def med(xs):
                    return sorted(xs)[len(xs) // 2] if xs else None

                tb, tbf = med(t_b), med(t_bf)
                rows.append({
                    "query_id": qid, "env_a": a, "env_b": b, "pair": f"{a}->{b}",
                    "plan_differed": int(sig_a != sig_b),
                    "sig_a": sig_a, "sig_b": sig_b,
                    "t_a_ms": ta, "t_b_ms": tb, "t_b_forced_a_ms": tbf,
                    "forced_verified": bool(verified and tbf),
                    "total": (tb / ta) if (tb and ta) else None,
                    "plan_effect": (tb / tbf) if (verified and tb and tbf) else None,
                    "settings_effect": (tbf / ta) if (verified and tbf and ta) else None,
                    "interleaved": True, "rounds": ROUNDS,
                    "n_hints": len(plan_to_hints(res_a["Plan"]).split()) - 2,
                })
            except psycopg2.Error as e:
                rows.append({"query_id": qid, "env_a": a, "env_b": b, "pair": f"{a}->{b}",
                             "forced_verified": False,
                             "error": str(e).splitlines()[0][:150]})
                cur = conn.cursor()
                cur.execute(f"set statement_timeout = {args.timeout_ms}")
                enable_hints(cur)
        pd.DataFrame(rows).to_csv(args.out, index=False)
        d = pd.DataFrame(rows)
        usable = int((d.get("plan_differed", 0) == 1).sum() and
                     ((d.get("plan_differed", 0) == 1) & d["forced_verified"]).sum())
        print(f"pair {a}->{b} done ({(time.time() - t0) / 60:.1f} min, "
              f"usable cells so far: {usable})", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(args.out, index=False)
    diff = df[df.get("plan_differed", 0) == 1]
    print(f"\nWrote {args.out} ({len(df)} rows)")
    if len(diff):
        print(f"cells where the plan differed: {len(diff)}, "
              f"of which forced+verified: {int(diff['forced_verified'].sum())} "
              f"({diff['forced_verified'].mean():.0%})")


if __name__ == "__main__":
    main()
