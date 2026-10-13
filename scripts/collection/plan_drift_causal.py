"""
Causal plan-drift decomposition via forced plans (CONFERENCE_PLAN.md item 3).

Tier 3 measured plan drift observationally and found ~16% of cross-environment runtime
variation *associated* with the plan changing. It could not separate cause from correlation,
because the settings that change the plan (work_mem, max_parallel_workers_per_gather) also
change execution.

With pg_hint_plan the separation is exact. For query q and environments A, B:

    T_A    = T(A, P_A)   native in A
    T_B    = T(B, P_B)   native in B
    T_B|PA = T(B, P_A)   B's settings, A's plan FORCED

    total    = T_B / T_A         = what a cross-environment predictor must explain
    plan     = T_B / T_B|PA      = pure plan effect, settings held at B
    settings = T_B|PA / T_A      = pure settings effect, plan held at A

and total = plan x settings identically, so the decomposition is complete, not a regression.

Any row whose forced plan does not verify (pg_hint_plan silently drops hints it cannot
apply) is recorded with verified=False and excluded from the decomposition.

Writes results/plan_drift_causal_raw.csv.
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan_to_hints import force_and_verify, plan_signature, run_explain  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_DIR / "results"

ENVIRONMENTS = {
    "small": {"max_parallel_workers_per_gather": 0, "effective_cache_size": "256MB",
              "work_mem": "1MB", "random_page_cost": 4.0},
    "mid": {"max_parallel_workers_per_gather": 2, "effective_cache_size": "4GB",
            "work_mem": "4MB", "random_page_cost": 4.0},
    "big": {"max_parallel_workers_per_gather": 8, "effective_cache_size": "16GB",
            "work_mem": "64MB", "random_page_cost": 1.1},
    "cores_only": {"max_parallel_workers_per_gather": 8, "effective_cache_size": "4GB",
                   "work_mem": "4MB", "random_page_cost": 4.0},
    "nvme_only": {"max_parallel_workers_per_gather": 2, "effective_cache_size": "4GB",
                  "work_mem": "4MB", "random_page_cost": 1.1},
    "memory_only": {"max_parallel_workers_per_gather": 2, "effective_cache_size": "4GB",
                    "work_mem": "64MB", "random_page_cost": 4.0},
}
# A is always the reference the dev machine would have profiled on.
PAIRS = [("mid", "big"), ("mid", "small"), ("mid", "cores_only"),
         ("mid", "nvme_only"), ("mid", "memory_only"), ("small", "big")]
MEASURED = 2


def median_run(cur, sql, settings, reps):
    """Warm once, then take the median of `reps` measured runs."""
    run_explain(cur, sql, settings)
    out = [run_explain(cur, sql, settings) for _ in range(reps)]
    times = sorted(r["Execution Time"] for r in out)
    return out[0], float(times[len(times) // 2])


def median_forced(cur, sql, target_plan, settings, reps):
    res, hints, ok = force_and_verify(cur, sql, target_plan, settings)
    if res is None or not ok:
        return None, None, hints, False
    times = []
    for _ in range(reps):
        r, _, ok2 = force_and_verify(cur, sql, target_plan, settings)
        if not ok2:
            return None, None, hints, False
        times.append(r["Execution Time"])
    times.sort()
    return res, float(times[len(times) // 2]), hints, True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="tpch")
    ap.add_argument("--queries", default="scripts/collection/queries")
    ap.add_argument("--out", default=str(RESULTS_DIR / "plan_drift_causal_raw.csv"))
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

    rows, t0 = [], time.time()
    # Native runs per (query, environment), reused across every pair.
    native = {}
    envs_needed = sorted({e for p in PAIRS for e in p})
    for env in envs_needed:
        for qid, sql in sqls.items():
            try:
                res, t = median_run(cur, sqls[qid], ENVIRONMENTS[env], MEASURED)
                native[(qid, env)] = (res, t)
            except psycopg2.Error as e:
                native[(qid, env)] = (None, None)
                print(f"  native {qid}/{env} failed: {str(e).splitlines()[0][:90]}")
                cur = conn.cursor()
                cur.execute(f"set statement_timeout = {args.timeout_ms}")
        print(f"native {env} done ({(time.time() - t0) / 60:.1f} min)", flush=True)

    for a, b in PAIRS:
        for qid, sql in sqls.items():
            ra, ta = native.get((qid, a), (None, None))
            rb, tb = native.get((qid, b), (None, None))
            if ra is None or rb is None:
                continue
            sig_a, sig_b = plan_signature(ra["Plan"]), plan_signature(rb["Plan"])
            try:
                rf, tf, hints, ok = median_forced(cur, sql, ra["Plan"],
                                                  ENVIRONMENTS[b], MEASURED)
            except psycopg2.Error as e:
                rf, tf, hints, ok = None, None, "", False
                print(f"  force {qid} {a}->{b} failed: {str(e).splitlines()[0][:90]}")
                cur = conn.cursor()
                cur.execute(f"set statement_timeout = {args.timeout_ms}")
                cur.execute("set pg_hint_plan.enable_hint = on")
            rows.append({
                "query_id": qid, "env_a": a, "env_b": b, "pair": f"{a}->{b}",
                "plan_differed": int(sig_a != sig_b),
                "sig_a": sig_a, "sig_b": sig_b,
                "t_a_ms": ta, "t_b_ms": tb, "t_b_forced_a_ms": tf,
                "forced_verified": bool(ok),
                "total": tb / ta if ta else None,
                "plan_effect": (tb / tf) if (ok and tf) else None,
                "settings_effect": (tf / ta) if (ok and tf and ta) else None,
                "n_hints": len(hints.split()) - 2 if hints else 0,
            })
        pd.DataFrame(rows).to_csv(args.out, index=False)
        ok_n = sum(1 for r in rows if r["forced_verified"])
        print(f"pair {a}->{b} done ({(time.time() - t0) / 60:.1f} min, "
              f"{ok_n}/{len(rows)} forced plans verified)", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(args.out, index=False)
    v = df[df["forced_verified"]]
    print(f"\nWrote {args.out} ({len(df)} rows, {len(v)} verified)")
    print(f"verification rate: {len(v) / len(df):.1%}")


if __name__ == "__main__":
    main()
