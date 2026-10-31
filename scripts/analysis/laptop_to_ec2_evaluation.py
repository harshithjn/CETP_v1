"""
Cross-ISA: can a developer's arm64 laptop predict runtime on real x86_64 production hardware?

This is the only experiment in the project that uses REAL production machines on a DIFFERENT
instruction set. dev is the Apple M1 (arm64, PostgreSQL 15.17); prod is the five EC2
instances the project was originally built on (c5a.xlarge, m5a.xlarge, r5n.large, z1d.large,
c7i.large -- x86_64, 4/4/2/2/2 vCPU).

WHY THE OPERATOR MODEL CANNOT BE RUN HERE. `operator_probe.py` needs to execute SQL on the
machine, and **the five EC2 instances are terminated.** No per-operator rates and no
parallel-speedup measurement exist for them, and none can be obtained. Deriving rates from
the very runtimes being predicted would be circular. So this evaluation tests the ONE
component that transfers without a new probe: few-shot calibration, on top of the best
available zero-shot scalar.

WHAT THIS DOES TEST, and it is the question that matters:
  * Does zero-shot cross-ISA prediction work?            (previously: 324%, then 153%)
  * Does measuring k queries on production rescue it?
  * Does the `spread` selection rule beat chance across an ISA boundary?

CAVEATS carried into every number below:
  * EC2 `max_parallel_workers_per_gather` is an INFERENCE (PostgreSQL's default of 2, and the
    collection guide never mentions tuning it), not a re-measurement -- the instances were
    gone before this could be checked. The M1 side is measured at 2.
  * q15 has no EC2 counterpart. q16 and q20 are APPROXIMATE reconstructions on the laptop
    side (results/query_reconstruction_validation.csv), so the laptop and EC2 rows describe
    slightly different queries; they are excluded from the primary result and reported
    separately.
  * The compute scalar is FP-vector GFLOPS, which this project has shown is the wrong
    quantity for database work. It is used here because it is the only signature that exists
    on both sides, not because it is good.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from cetp.operator_model import (fit_calibration,                   # noqa: E402
                                 select_calibration_queries)

# The 22-query x 21-repetition laptop collection -- the same one behind the published
# 324% / 153% figures in results/tier1_laptop_dev.md. tpch_m1_c1.csv is the concurrency
# sweep (4 reps) and gives a different warm median, so it must not be substituted here.
DEV_CSV = PROJECT_DIR / "data" / "laptop" / "tpch_dataset_m1.csv"
EC2_CSV = PROJECT_DIR / "data" / "corrected" / "tpch_dataset_corrected_v2.csv"
RESULTS = PROJECT_DIR / "results"
DISCARD_WARMUP_REPS = 1

# Multi-core aggregate GFLOPS, the signature the project's headline requires.
EC2_COMPUTE = {"c5a": 40.8071, "z1d": 28.0804, "r5n": 24.9681,
               "m5a": 31.5366, "c7i": 25.8909}
EC2_VCPU = {"c5a": 4, "m5a": 4, "r5n": 2, "z1d": 2, "c7i": 2}

# The M1's score depends entirely on how many threads the probe used. This IS the bug:
# a query can only ever use 1 + max_parallel_workers_per_gather = 3 processes.
M1_COMPUTE = {"nproc8": 61.82, "dop3": 35.62}

EXCLUDE_APPROXIMATE = ["q16", "q20"]     # laptop reconstructions differ from the EC2 query


def warm_median_by_query(df, time_col="time_ms", rep_col="rep"):
    out = {}
    for q, g in df.groupby("query_id"):
        t = (g.sort_values(rep_col)[time_col].to_numpy() if rep_col in g
             else g[time_col].to_numpy())
        t = t[DISCARD_WARMUP_REPS:] if len(t) > DISCARD_WARMUP_REPS else t
        if len(t):
            out[q] = float(np.median(t))
    return out


def mape(y, p):
    return float(np.mean(np.abs((np.asarray(y) - np.asarray(p)) / np.asarray(y))) * 100)


def mdape(y, p):
    return float(np.median(np.abs((np.asarray(y) - np.asarray(p)) / np.asarray(y))) * 100)


def qstats(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    q = np.maximum(p / y, y / p)
    return float(np.median(q)), float(np.percentile(q, 95))


def score(y, p):
    q50, q95 = qstats(y, p)
    return {"mape": mape(y, p), "mdape": mdape(y, p),
            "qerror_median": q50, "qerror_p95": q95, "n": int(len(y))}


def build(exclude_approximate=True):
    dev = pd.read_csv(DEV_CSV)
    if "ok" in dev:
        dev = dev[dev["ok"] == True]                                 # noqa: E712
    ec2 = pd.read_csv(EC2_CSV)
    dev_t = warm_median_by_query(dev)
    rows = []
    for m, g in ec2.groupby("machine_id"):
        prod_t = warm_median_by_query(g, rep_col="rep" if "rep" in g else "query_id")
        for q in sorted(set(dev_t) & set(prod_t)):
            if exclude_approximate and q in EXCLUDE_APPROXIMATE:
                continue
            d, p = dev_t[q], prod_t[q]
            rows.append({
                "prod": m, "query_id": q, "dev_ms": d, "actual_prod_ms": p,
                "actual_scaling": p / d,
                "naive": 1.0,
                # 1/compute_ratio, with the M1 scored two ways
                "cmp_nproc8": M1_COMPUTE["nproc8"] / EC2_COMPUTE[m],
                "cmp_dop3": M1_COMPUTE["dop3"] / EC2_COMPUTE[m],
                "vcpu": EC2_VCPU[m],
            })
    return pd.DataFrame(rows)


PREDICTORS = ["naive", "cmp_nproc8", "cmp_dop3"]


def few_shot(df, predictor, ks=(0, 1, 2, 3, 5, 8), seed=42):
    """Calibrate on k revealed queries per production machine, score on the rest."""
    rng = np.random.default_rng(seed)
    out = []
    for k in ks:
        for rule in ("spread", "random"):
            if k == 0 and rule == "random":
                continue
            errs, errs_naive, n, degenerate = [], [], 0, False
            for m, g in df.groupby("prod"):
                qs = sorted(g["query_id"].unique())
                if k >= len(qs):
                    continue
                if k == 0:
                    reveal = []
                elif rule == "spread":
                    cand = g.set_index("query_id")[predictor].to_dict()
                    if len(set(round(v, 12) for v in cand.values())) < 2:
                        # Predictor is constant across queries -> `spread` is undefined.
                        reveal = None
                    else:
                        reveal = select_calibration_queries(cand, k)
                else:
                    reveal = list(rng.choice(qs, size=k, replace=False))
                if reveal is None:
                    degenerate = True
                    continue
                seen = g[g["query_id"].isin(reveal)]
                test = g[~g["query_id"].isin(reveal)]
                if test.empty:
                    continue
                c = (fit_calibration(list(zip(seen[predictor], seen["actual_scaling"])))
                     if k else 1.0)
                y = test["actual_prod_ms"].to_numpy()
                errs.append(mape(y, (test["dev_ms"] * test[predictor] * c).to_numpy()))
                errs_naive.append(mape(y, test["dev_ms"].to_numpy()))
                n += len(test)
            if degenerate and not errs:
                out.append({"k": k, "rule": rule, "mape": None, "naive_mape": None,
                            "n_scored": 0, "inapplicable":
                            "predictor is constant across queries, so `spread` has no "
                            "range to spread over"})
            elif errs:
                out.append({"k": k, "rule": rule, "mape": float(np.mean(errs)),
                            "naive_mape": float(np.mean(errs_naive)), "n_scored": n})
    return out


def scalar_floor(df):
    """
    The best MAPE any PER-MACHINE-SCALAR predictor can reach on this data.

    Every predictor in this project's lineage -- bandwidth ratio, compute ratio, the
    analytical roofline, and anything plus a per-machine calibration -- multiplies the dev
    runtime by one number per (dev, prod) machine pair. Such a model cannot express
    variation in the scaling factor ACROSS QUERIES on the same machine. Dividing out each
    machine's median scaling leaves exactly that irreducible residual, and the mean absolute
    deviation of the residual from 1.0 is the floor.

    This is the number that decides whether a scalar model can ever work on a given
    environment pair, and it is computed from the measurements alone -- no model involved.
    """
    d = df.copy()
    d["resid"] = d.groupby("prod")["actual_scaling"].transform(lambda s: s / s.median())
    per = {}
    for m, g in d.groupby("prod"):
        s = g["actual_scaling"]
        per[m] = {"median_scaling": float(s.median()),
                  "min": float(s.min()), "max": float(s.max()),
                  "max_over_min": float(s.max() / s.min()),
                  "geometric_sd": float(np.exp(np.std(np.log(s))))}
    return {
        "mape_floor_pct": float(np.mean(np.abs(d["resid"] - 1.0)) * 100),
        "residual_max_over_min": float(d["resid"].max() / d["resid"].min()),
        "per_machine": per,
    }


def main():
    df = build(exclude_approximate=True)
    df_all = build(exclude_approximate=False)
    print(f"dev: Apple M1 (arm64)   prod: {sorted(df['prod'].unique())} (x86_64)")
    print(f"pairs: {len(df)} ({df['query_id'].nunique()} queries x "
          f"{df['prod'].nunique()} production machines), "
          f"excluding {EXCLUDE_APPROXIMATE} as approximate reconstructions")
    print(f"   with them included: {len(df_all)} pairs")

    y = df["actual_prod_ms"].to_numpy()
    res = {}
    print(f"\n{'predictor':14s} {'MAPE':>8s} {'MdAPE':>8s} {'q50':>6s} {'q95':>6s}")
    for p in PREDICTORS:
        pred = df["dev_ms"].to_numpy() * df[p].to_numpy()
        res[p] = score(y, pred)
        print(f"{p:14s} {res[p]['mape']:8.2f} {res[p]['mdape']:8.2f} "
              f"{res[p]['qerror_median']:6.3f} {res[p]['qerror_p95']:6.3f}")

    print(f"\nmean actual scaling (prod/dev): {df['actual_scaling'].mean():.3f}   "
          f"median {df['actual_scaling'].median():.3f}")
    print("  (<1 means the EC2 instances are FASTER than the laptop on this workload)")
    print(f"mean predicted scaling, cmp_nproc8: {df['cmp_nproc8'].mean():.3f}   "
          f"cmp_dop3: {df['cmp_dop3'].mean():.3f}")

    curves = {p: few_shot(df, p) for p in ("cmp_dop3", "cmp_nproc8")}
    for p, curve in curves.items():
        print(f"\nfew-shot calibration on {p}, scored only on UNREVEALED queries:")
        print(f"  {'k':>3s} {'spread':>9s} {'random':>9s} {'naive':>9s}")
        by_k = {}
        for r in curve:
            by_k.setdefault(r["k"], {})[r["rule"]] = r
        for k in sorted(by_k):
            sp = by_k[k].get("spread")
            rd = by_k[k].get("random")
            nv = next((x["naive_mape"] for x in (sp, rd)
                       if x and x.get("naive_mape") is not None), float("nan"))
            sp_s = (f"{sp['mape']:9.2f}" if (sp and sp.get("mape") is not None)
                    else "      n/a")
            rd_s = (f"{rd['mape']:9.2f}" if (rd and rd.get("mape") is not None)
                    else "        -")
            print(f"  {k:3d} {sp_s} {rd_s} {nv:9.2f}")

    print(f"\nper production machine (cmp_dop3, zero-shot):")
    print(f"{'prod':6s} {'vCPU':>5s} {'n':>3s} {'naive MAPE':>11s} {'cmp_dop3 MAPE':>14s} "
          f"{'actual scaling':>15s}")
    per = {}
    for m, g in df.groupby("prod"):
        yy = g["actual_prod_ms"].to_numpy()
        a = mape(yy, g["dev_ms"].to_numpy())
        b = mape(yy, (g["dev_ms"] * g["cmp_dop3"]).to_numpy())
        per[m] = {"vcpu": int(g["vcpu"].iloc[0]), "n": int(len(g)),
                  "naive_mape": a, "cmp_dop3_mape": b,
                  "median_actual_scaling": float(g["actual_scaling"].median())}
        print(f"{m:6s} {per[m]['vcpu']:5d} {len(g):3d} {a:11.2f} {b:14.2f} "
              f"{per[m]['median_actual_scaling']:15.3f}")

    floor = scalar_floor(df)
    print(f"\nirreducible floor for ANY per-machine-scalar predictor on this data:")
    print(f"  MAPE floor      {floor['mape_floor_pct']:.1f}%")
    print(f"  residual spread {floor['residual_max_over_min']:.1f}x max/min after removing "
          f"each machine's median scaling")
    print(f"  per-machine scaling spread: "
          + ", ".join(f"{m} {v['max_over_min']:.0f}x" for m, v in floor["per_machine"].items()))
    print("  => no scalar model, however calibrated, can beat this. Per-query variation "
          "needs per-QUERY capability information, i.e. per-operator rates.")

    payload = {
        "dev": "m1-host (Apple M1, arm64, PostgreSQL 15.17)",
        "scalar_predictor_floor": floor,
        "prod": sorted(df["prod"].unique()),
        "prod_arch": "x86_64 (EC2 c5a/m5a xlarge 4 vCPU; r5n/z1d/c7i large 2 vCPU)",
        "cross_isa": True,
        "n_pairs": int(len(df)), "n_queries": int(df["query_id"].nunique()),
        "excluded_queries": {"q15": "no EC2 counterpart",
                             "q16": "approximate laptop reconstruction",
                             "q20": "approximate laptop reconstruction"},
        "operator_model_not_applicable":
            "the five EC2 instances are terminated; operator_probe.py cannot be run on them, "
            "so no operator rates or parallel speedup exist and none can be obtained",
        "ec2_mpwpg_is_an_inference": True,
        "predictors_zero_shot": res,
        "few_shot": curves,
        "per_prod_machine": per,
        "mean_actual_scaling": float(df["actual_scaling"].mean()),
        "median_actual_scaling": float(df["actual_scaling"].median()),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "laptop_to_ec2_metrics.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n")
    df.to_csv(RESULTS / "laptop_to_ec2_pairs.csv", index=False)
    print(f"\nwrote {RESULTS / 'laptop_to_ec2_metrics.json'}")


if __name__ == "__main__":
    main()
