"""
The thesis, measured: predict production runtime from a real developer's laptop.

The container grid (`operator_model_evaluation.py`) pairs containers with containers. This
pairs the ACTUAL development machine -- the Apple M1 host, running its own PostgreSQL 15
cluster -- against the constrained container environments, which is the scenario the
project exists to serve.

The dev and prod sides here share planner-relevant settings (work_mem=4MB,
max_parallel_workers_per_gather=2, single client, identical SF1 data), verified below, so
the only thing differing is capability. They do NOT differ in ISA: the containers run on
the same arm64 host. Cross-ISA remains unmeasured and is the next thing to get.

Only cells whose operator probe passed the repeatability gate are used as production
targets, because a contended probe is not a capability measurement.
"""

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from cetp import cross_env                                            # noqa: E402

DEV_CSV = PROJECT_DIR / "data" / "laptop" / "tpch_m1_c1.csv"
DEV_PLANS = PROJECT_DIR / "data" / "plans" / "m1" / "tpch" / "c1"
GRID_DIR = PROJECT_DIR / "data" / "container_grid"
REGISTRY = PROJECT_DIR / "models" / "operator_signature.json"
DEV_ID = "m1-host"
RESULTS = PROJECT_DIR / "results"
DISCARD_WARMUP_REPS = 1


def warm_median(df, qid):
    g = df[(df["query_id"] == qid) & (df["ok"] == True)].sort_values("rep")  # noqa: E712
    t = g["time_ms"].to_numpy()
    if len(t) <= DISCARD_WARMUP_REPS:
        return None
    return float(np.median(t[DISCARD_WARMUP_REPS:]))


def settings(df):
    cols = ["pg_work_mem", "pg_max_parallel_workers_per_gather", "concurrency",
            "pg_shared_buffers", "pg_effective_cache_size", "pg_random_page_cost"]
    return {c: sorted(map(str, df[c].dropna().unique())) for c in cols if c in df}


def load_dev_plan(qid):
    hits = sorted(DEV_PLANS.glob(f"{qid}_r0_c0_*.json.gz"))
    if not hits:
        return None
    import gzip
    with gzip.open(hits[0], "rt") as fh:
        return json.load(fh)


def mape(y, p):
    return float(np.mean(np.abs((np.asarray(y) - np.asarray(p)) / np.asarray(y))) * 100)


def mdape(y, p):
    return float(np.median(np.abs((np.asarray(y) - np.asarray(p)) / np.asarray(y))) * 100)


def qstats(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    q = np.maximum(p / y, y / p)
    return float(np.median(q)), float(np.percentile(q, 95))


def main():
    reg = json.loads(REGISTRY.read_text())
    dev = pd.read_csv(DEV_CSV)
    dev_rates, dev_rec = cross_env.load_rates(str(REGISTRY), DEV_ID)
    dev_speedup = dev_rec.get("parallel_speedup") or 1.0

    prod_cells = sorted(m for m, v in reg.items()
                        if m != DEV_ID and v.get("reliable") and m.startswith("c"))
    if not prod_cells:
        sys.exit("no reliable container cells to use as production targets")
    print(f"dev: {DEV_ID} (speedup {dev_speedup:.3f}x)")
    print(f"prod targets (probe passed the repeatability gate): {prod_cells}")

    dev_settings = settings(dev)
    rows, mismatches = [], {}
    for cell in prod_cells:
        prod = pd.read_csv(GRID_DIR / f"tpch_{cell}.csv")
        ps = settings(prod)
        diff = {k: (dev_settings.get(k), ps.get(k)) for k in dev_settings
                if dev_settings.get(k) != ps.get(k)}
        if diff:
            mismatches[cell] = diff
        prod_rates, prod_rec = cross_env.load_rates(str(REGISTRY), cell)
        cross_env.assert_comparable(dev_rec, prod_rec)
        sp = prod_rec.get("parallel_speedup") or 1.0

        for qid in sorted(set(dev["query_id"]) & set(prod["query_id"])):
            d_ms, p_ms = warm_median(dev, qid), warm_median(prod, qid)
            plan = load_dev_plan(qid)
            if d_ms is None or p_ms is None or plan is None:
                continue
            out = cross_env.predict(None, plan, d_ms, dev_rates, prod_rates,
                                    plan_forced=None, dev_speedup=dev_speedup,
                                    prod_speedup=sp)
            rows.append({
                "prod": cell, "query_id": qid, "dev_ms": d_ms, "actual_prod_ms": p_ms,
                "predicted_prod_ms": out["predicted_prod_ms"],
                "actual_scaling": p_ms / d_ms, "predicted_scaling": out["scaling"],
                "naive_ms": d_ms, "explained_frac": out["explained_frac"],
                "unmapped_frac": out["unmapped_frac"], "parallel": out["parallel"],
            })

    if mismatches:
        print("\nWARNING: planner-relevant settings differ between dev and these cells, so "
              "the comparison is not purely a capability comparison:")
        for c, d in mismatches.items():
            print(f"  {c}: {d}")
    else:
        print("settings check: dev and every prod cell agree on "
              f"{', '.join(sorted(dev_settings))}")

    df = pd.DataFrame(rows)
    if df.empty:
        sys.exit("no usable (query, cell) pairs")
    y = df["actual_prod_ms"].to_numpy()
    res = {}
    for name, pred in {"operator": df["predicted_prod_ms"].to_numpy(),
                       "naive": df["naive_ms"].to_numpy()}.items():
        q50, q95 = qstats(y, pred)
        res[name] = {"mape": mape(y, pred), "mdape": mdape(y, pred),
                     "qerror_median": q50, "qerror_p95": q95}

    print(f"\nlaptop -> production, {len(df)} (query, machine) pairs "
          f"over {df['prod'].nunique()} production machines, {df['query_id'].nunique()} queries")
    print(f"{'predictor':12s} {'MAPE':>7s} {'MdAPE':>7s} {'q50':>6s} {'q95':>6s}")
    for k in ("naive", "operator"):
        r = res[k]
        print(f"{k:12s} {r['mape']:7.2f} {r['mdape']:7.2f} "
              f"{r['qerror_median']:6.3f} {r['qerror_p95']:6.3f}")

    print(f"\nper production machine:")
    print(f"{'prod':8s} {'n':>3s} {'naive MAPE':>11s} {'operator MAPE':>14s}")
    per_cell = {}
    for cell, g in df.groupby("prod"):
        yy = g["actual_prod_ms"].to_numpy()
        a = mape(yy, g["naive_ms"].to_numpy())
        b = mape(yy, g["predicted_prod_ms"].to_numpy())
        per_cell[cell] = {"n": int(len(g)), "naive_mape": a, "operator_mape": b}
        print(f"{cell:8s} {len(g):3d} {a:11.2f} {b:14.2f}")

    # ---- move 4 in its actual use case -------------------------------------------
    # The errors above are a systematic per-machine BIAS (mean signed error +0.19 to
    # +0.65), not scatter: the probe is cache-resident while TPC-H reads far past
    # shared_buffers, so the probe mis-ranks a host cluster against a container. One
    # multiplicative correction per production machine is precisely the right shape of fix,
    # and unlike the container grid -- where all cells were homogeneous and there was no
    # bias to remove -- this is the case move 4 was designed for.
    #
    # Honest protocol: calibrate on k revealed queries and score ONLY on the queries not
    # revealed, per production machine. Both selection rules are reported, because the
    # cold-start study found random selection at k=1 could be worse than not calibrating.
    from cetp.operator_model import fit_calibration, select_calibration_queries
    rng = np.random.default_rng(42)
    curve = []
    for k in (0, 1, 2, 3, 5, 8):
        for rule in ("spread", "random"):
            if k == 0 and rule == "random":
                continue
            errs_op, errs_naive, n = [], [], 0
            for cell, g in df.groupby("prod"):
                qs = sorted(g["query_id"].unique())
                if k >= len(qs):
                    continue
                if k == 0:
                    reveal = []
                elif rule == "spread":
                    reveal = select_calibration_queries(
                        g.set_index("query_id")["predicted_scaling"].to_dict(), k)
                else:
                    reveal = list(rng.choice(qs, size=k, replace=False))
                seen = g[g["query_id"].isin(reveal)]
                test = g[~g["query_id"].isin(reveal)]
                if test.empty:
                    continue
                c = (fit_calibration(list(zip(seen["predicted_scaling"],
                                              seen["actual_scaling"]))) if k else 1.0)
                yy = test["actual_prod_ms"].to_numpy()
                errs_op.append(mape(yy, (test["predicted_prod_ms"] * c).to_numpy()))
                errs_naive.append(mape(yy, test["naive_ms"].to_numpy()))
                n += len(test)
            if errs_op:
                curve.append({"k": k, "rule": rule,
                              "operator_mape": float(np.mean(errs_op)),
                              "naive_mape": float(np.mean(errs_naive)),
                              "n_scored": n})

    print("\nfew-shot calibration, scored only on UNREVEALED queries:")
    print(f"  {'k':>3s} {'spread':>9s} {'random':>9s} {'naive':>9s}")
    by_k = {}
    for r in curve:
        by_k.setdefault(r["k"], {})[r["rule"]] = r
    for k in sorted(by_k):
        sp_ = by_k[k].get("spread")
        rd_ = by_k[k].get("random")
        nv = (sp_ or rd_)["naive_mape"]
        print(f"  {k:3d} {sp_['operator_mape'] if sp_ else float('nan'):9.2f} "
              f"{(rd_['operator_mape'] if rd_ else float('nan')):9.2f} {nv:9.2f}")

    worst = df.assign(err=lambda x: (x["predicted_prod_ms"] - x["actual_prod_ms"]).abs()
                      / x["actual_prod_ms"] * 100).nlargest(5, "err")
    print("\nworst 5 predictions (these are where the model is wrong, and why it is "
          "reported):")
    for _, r in worst.iterrows():
        print(f"  {r['prod']:8s} {r['query_id']:4s} dev {r['dev_ms']:9.1f} -> "
              f"predicted {r['predicted_prod_ms']:9.1f} vs actual "
              f"{r['actual_prod_ms']:9.1f}  ({r['err']:.0f}% off)")

    payload = {
        "dev": DEV_ID, "dev_parallel_speedup": dev_speedup,
        "prod_cells": prod_cells, "n_pairs": int(len(df)),
        "n_queries": int(df["query_id"].nunique()),
        "settings_mismatches": mismatches,
        "same_isa_caveat": "all prod cells are containers on the same arm64 host as dev; "
                           "this measures capability transfer, not ISA transfer",
        "predictors": res, "per_prod_machine": per_cell,
        "few_shot": curve,
        "signed_bias_per_machine": {
            c: float(((g["predicted_prod_ms"] - g["actual_prod_ms"])
                      / g["actual_prod_ms"]).mean())
            for c, g in df.groupby("prod")},
        "mean_explained_frac": float(df["explained_frac"].mean()),
        "mean_unmapped_frac": float(df["unmapped_frac"].mean()),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "laptop_to_prod_metrics.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n")
    df.to_csv(RESULTS / "laptop_to_prod_pairs.csv", index=False)
    print(f"\nwrote {RESULTS / 'laptop_to_prod_metrics.json'}")


if __name__ == "__main__":
    main()
