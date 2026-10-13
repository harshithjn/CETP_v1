"""
Does per-operator capability beat a single capability scalar?

This is the evaluation for the four moves in cetp/cross_env.py, run on the container
capability grid (CPU quota x memory quota, orthogonal by construction -- which is the one
thing renting more EC2 instance types cannot buy, because instance types are correlated
bundles: the five-machine EC2 signature correlates at r=+0.958 with 97.9% of variance on
one component).

PREDICTORS COMPARED. All operate on the same (dev, prod, query) pairs.

  naive         scaling = 1.0                       assume production equals development
  scalar_seq    rate_dev(seqscan)/rate_prod(seqscan)  ONE probe axis, the single-scalar
                                                      model done as well as it can be
  scalar_geo    geomean over axes of the rate ratio   single scalar, best-effort summary
  operator      weighted per-operator (cetp.operator_model)

The comparison that matters is `operator` against `scalar_*`. If per-operator weighting
does not beat a single scalar, moves 1 and 2 do not earn their complexity and that must be
reported -- the same standard applied to the analytical roofline, which turned out to be
indistinguishable from `1/compute_ratio`.

PROTOCOL. Leave-one-machine-out. Note that the uncalibrated predictors have NO fitted
parameters, so their LOMO score equals their all-pairs score; the fold structure matters
only for the calibrated variant, where the correction is fit on k revealed queries of the
held-out production machine and scored on the queries NOT revealed. That split is what
makes the few-shot number honest rather than a fit to its own test set.
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from cetp.operator_model import (AXES, fit_calibration, predict_scaling,  # noqa: E402
                                 select_calibration_queries)

GRID_DIR = PROJECT_DIR / "data" / "container_grid"
PLANS_DIR = PROJECT_DIR / "data" / "plans_grid"
RESULTS = PROJECT_DIR / "results"
SIG = GRID_DIR / "operator_signature_grid.json"
DISCARD_WARMUP_REPS = 1


def load_grid():
    """(machine, query) -> warm median runtime, and the plan file for the weights."""
    rows = []
    for csv in sorted(GRID_DIR.glob("tpch_*.csv")):
        df = pd.read_csv(csv)
        df = df[df["ok"] == True]                                   # noqa: E712
        rows.append(df)
    if not rows:
        sys.exit(f"no grid CSVs in {GRID_DIR}; run container_grid.py --collect first")
    raw = pd.concat(rows, ignore_index=True)

    def warm_median(g):
        t = g.sort_values("rep")["time_ms"].to_numpy()
        t = t[DISCARD_WARMUP_REPS:] if len(t) > DISCARD_WARMUP_REPS else t
        return float(np.median(t))

    cells = []
    load_grid.raw = raw
    for (mid, qid), g in raw.groupby(["machine_id", "query_id"]):
        pf = g.sort_values("rep")["plan_json_file"].dropna()
        cells.append({
            "machine_id": mid, "query_id": qid,
            "time_ms": warm_median(g),
            "n_reps": len(g),
            "plan_file": pf.iloc[0] if len(pf) else None,
            "dop": int(g["dop"].iloc[0]) if "dop" in g else None,
        })
    return pd.DataFrame(cells)


def plan_path(machine_id, plan_file):
    if not plan_file:
        return None
    hits = list((PLANS_DIR / machine_id).rglob(plan_file))
    return hits[0] if hits else None


def load_plan(machine_id, plan_file):
    p = plan_path(machine_id, plan_file)
    if p is None:
        return None
    import gzip
    opener = gzip.open if p.suffix == ".gz" else open
    with opener(p, "rt") as fh:
        return json.load(fh)


def plan_agreement(cells_raw):
    """
    Do the grid cells choose the same plan for the same query?

    This decides what the grid can and cannot test. Resource QUOTAS do not change the
    planner's cost parameters -- shared_buffers, effective_cache_size, work_mem and
    random_page_cost are pinned identical across every cell -- so the plan should be
    stable and the grid should isolate the capability term cleanly. Confirmed empirically
    rather than assumed, because if plans DID differ the measured error would mix the
    capability term with plan divergence, which is 70.5% of the cross-environment
    difference when it occurs.

    The corollary is a limitation: with no plan divergence in the grid, **move 3 (take the
    plan from production) cannot be evaluated here at all**.
    """
    per_query = {}
    for (q, ), g in cells_raw.groupby(["query_id"]):
        per_query[q] = sorted(set(g["plan_signature"].dropna()))
    differing = {q: v for q, v in per_query.items() if len(v) > 1}
    return {
        "n_queries": len(per_query),
        "n_queries_with_differing_plans": len(differing),
        "differing": sorted(differing),
        "isolates_capability_term": not differing,
        "move3_evaluable_here": bool(differing),
    }


def geomean(xs):
    xs = [x for x in xs if x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else 1.0


def build_pairs(cells, sigs, speedups):
    """Ordered dev->prod pairs with every predictor's scaling factor attached."""
    machines = sorted(cells["machine_id"].unique())
    by = {m: cells[cells["machine_id"] == m].set_index("query_id") for m in machines}
    plan_cache = {}
    rows, skipped = [], {"no_plan": 0, "no_sig": 0, "unexplained": 0}

    for dev in machines:
        for prod in machines:
            if dev == prod:
                continue
            if dev not in sigs or prod not in sigs:
                skipped["no_sig"] += 1
                continue
            dr, pr = sigs[dev], sigs[prod]
            for q in sorted(by[dev].index.intersection(by[prod].index)):
                d, p = by[dev].loc[q], by[prod].loc[q]
                key = (dev, d["plan_file"])
                if key not in plan_cache:
                    plan_cache[key] = load_plan(dev, d["plan_file"])
                plan = plan_cache[key]
                if plan is None:
                    skipped["no_plan"] += 1
                    continue

                res = predict_scaling(plan, dr, pr,
                                      dev_speedup=speedups.get(dev, 1.0),
                                      prod_speedup=speedups.get(prod, 1.0))
                # THE ABLATION THAT ISOLATES WHAT EARNS THE GAIN.
                # `operator` differs from `scalar_geo_par` in TWO ways at once: it weights
                # per-operator RATES, and it applies the parallelism correction only to the
                # part of the plan that is actually below a Gather. With clean probes the
                # serial axes span just 1.01-1.13x across this grid, so per-operator rate
                # weighting can hardly matter -- which means the gain should be entirely
                # the localisation. This predictor tests that: ONE serial scalar for the
                # whole plan, but parallelism applied only to the parallel wall-time share.
                # If it matches `operator`, the contribution is plan-structure
                # decomposition, NOT per-operator rate differentiation, and the paper must
                # say so.
                wbp = res["weights_by_parallelism"]
                w_par = sum(v for k, v in wbp.items() if k.endswith("(par)"))
                # The same model WITHOUT the parallelism axis, to show what that axis buys.
                res_serial = predict_scaling(plan, dr, pr,
                                             dev_speedup=1.0, prod_speedup=1.0)
                if res["explained_frac"] < 0.5:
                    skipped["unexplained"] += 1
                    continue

                ratios = [dr[a] / pr[a] for a in AXES if a in dr and a in pr]
                sd, sp = speedups.get(dev, 1.0), speedups.get(prod, 1.0)
                rows.append({
                    "dev": dev, "prod": prod, "query_id": q,
                    "dev_ms": d["time_ms"], "prod_ms": p["time_ms"],
                    "actual_scaling": p["time_ms"] / d["time_ms"],
                    "naive": 1.0,
                    "scalar_seq": dr["seqscan"] / pr["seqscan"],
                    "scalar_geo": geomean(ratios),
                    # A single scalar given the SAME parallelism information the operator
                    # model gets, so the comparison isolates per-operator weighting rather
                    # than rewarding the operator model for an axis the baseline lacks.
                    "scalar_geo_par": geomean(ratios) * (sd / sp),
                    "scalar_localized_par": (geomean(ratios)
                                             * (w_par * (sd / sp) + (1.0 - w_par))),
                    "operator_serial": res_serial["scaling"],
                    "operator": res["scaling"],
                    "unmapped_frac": res["unmapped_frac"],
                    "explained_frac": res["explained_frac"],
                    "parallel": res["parallel"],
                })
    return pd.DataFrame(rows), skipped


PREDICTORS = ["naive", "scalar_seq", "scalar_geo", "scalar_geo_par",
              "scalar_localized_par", "operator_serial", "operator"]


def mape(y, p):
    return float(np.mean(np.abs((np.asarray(y) - np.asarray(p)) / np.asarray(y))) * 100)


def mdape(y, p):
    return float(np.median(np.abs((np.asarray(y) - np.asarray(p)) / np.asarray(y))) * 100)


def qerr(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    e = 1e-12
    q = np.maximum(np.maximum(p, e) / np.maximum(y, e), np.maximum(y, e) / np.maximum(p, e))
    return float(np.median(q)), float(np.percentile(q, 95))


def score(y, p):
    q50, q95 = qerr(y, p)
    return {"mape": mape(y, p), "mdape": mdape(y, p),
            "qerror_median": q50, "qerror_p95": q95}


def cluster_bootstrap(pairs, a, b, n=2000, seed=42):
    """Paired bootstrap on MAPE difference (a - b), resampling whole prod machines."""
    rng = np.random.default_rng(seed)
    groups = [g for _, g in pairs.groupby("prod")]
    diffs = np.empty(n)
    for i in range(n):
        s = pd.concat([groups[j] for j in rng.integers(0, len(groups), len(groups))],
                      ignore_index=True)
        y = s["actual_scaling"].to_numpy()
        diffs[i] = mape(y, s[a].to_numpy()) - mape(y, s[b].to_numpy())
    y = pairs["actual_scaling"].to_numpy()
    point = mape(y, pairs[a].to_numpy()) - mape(y, pairs[b].to_numpy())
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"point_pp": float(point), "ci_low_pp": float(lo), "ci_high_pp": float(hi),
            "excludes_zero": bool(lo > 0 or hi < 0)}


def few_shot_curve(pairs, predictor="operator", ks=(0, 1, 2, 3, 5, 8)):
    """
    Leave-one-machine-out few-shot calibration.

    For each production machine and each k: choose k queries by `spread` over the predicted
    scalings, fit one multiplicative correction on those, and score on the queries NOT
    revealed. Reported both for the `spread` rule and for a random rule, because the
    cold-start study found random selection at k=1 made things WORSE than not calibrating.
    """
    out = []
    rng = np.random.default_rng(42)
    for k in ks:
        for rule in ("spread", "random"):
            if k == 0 and rule == "random":
                continue
            errs = []
            for prod, g in pairs.groupby("prod"):
                qs = sorted(g["query_id"].unique())
                if k >= len(qs):
                    continue
                if k == 0:
                    reveal = []
                elif rule == "spread":
                    cand = g.groupby("query_id")[predictor].median().to_dict()
                    reveal = select_calibration_queries(cand, k)
                else:
                    reveal = list(rng.choice(qs, size=k, replace=False))
                seen = g[g["query_id"].isin(reveal)]
                test = g[~g["query_id"].isin(reveal)]
                if test.empty:
                    continue
                c = (fit_calibration(list(zip(seen[predictor], seen["actual_scaling"])))
                     if k else 1.0)
                errs.append(mape(test["actual_scaling"].to_numpy(),
                                 (test[predictor] * c).to_numpy()))
            if errs:
                out.append({"k": k, "rule": rule, "mape": float(np.mean(errs)),
                            "n_machines": len(errs)})
    return out


def axis_dispersion(sigs, speedups):
    """
    How much does each axis actually VARY across the grid? This bounds what the grid can
    possibly discriminate.

    Expected result, and it is a property of the instrument rather than of the model: a CPU
    quota does not slow a SINGLE-THREADED operator down, because one worker only needs one
    core. So the serial axes should be nearly constant across the CPU levels while
    `parallel_speedup` moves. If that holds, this grid tests the PARALLELISM axis well and
    cannot test per-operator weighting at all -- for that, the axes have to differ, which
    needs genuinely different hardware (a second ISA), not a quota.
    """
    out = {}
    for a in AXES:
        v = np.array([sigs[m][a] for m in sorted(sigs) if a in sigs[m]], float)
        if len(v) < 2:
            continue
        out[a] = {"min": float(v.min()), "max": float(v.max()),
                  "spread": float(v.max() / v.min()),
                  "cv_across_cells": float(v.std() / v.mean())}
    sp = np.array([speedups[m] for m in sorted(speedups)], float)
    out["parallel_speedup"] = {"min": float(sp.min()), "max": float(sp.max()),
                               "spread": float(sp.max() / sp.min()),
                               "cv_across_cells": float(sp.std() / sp.mean())}
    serial = [out[a]["spread"] for a in AXES if a in out]
    out["_verdict"] = {
        "max_serial_axis_spread": float(max(serial)) if serial else None,
        "parallel_speedup_spread": out["parallel_speedup"]["spread"],
        "grid_varies_mainly": ("parallelism"
                               if out["parallel_speedup"]["spread"] > (max(serial) if serial else 0)
                               else "serial rates"),
    }
    return out


def axis_collinearity(sigs):
    """Is the operator signature more than one axis? The old one was not."""
    ms = sorted(sigs)
    M = np.array([[sigs[m][a] for a in AXES] for m in ms], float)
    L = np.log(M)
    Z = (L - L.mean(0)) / (L.std(0) + 1e-12)
    C = np.corrcoef(Z.T)
    _, sv, _ = np.linalg.svd(Z - Z.mean(0), full_matrices=False)
    var = (sv ** 2) / (sv ** 2).sum()
    off = [float(C[i, j]) for i in range(len(AXES)) for j in range(i + 1, len(AXES))]
    return {
        "axes": AXES, "machines": ms,
        "correlation_matrix": C.tolist(),
        "mean_abs_offdiagonal_r": float(np.mean(np.abs(off))),
        "max_abs_offdiagonal_r": float(np.max(np.abs(off))),
        "pca_explained_variance_ratio": var.tolist(),
        "pc1_share": float(var[0]),
        "effective_rank_95pct": int(np.searchsorted(np.cumsum(var), 0.95) + 1),
    }


def main():
    if not SIG.exists():
        sys.exit(f"no operator signatures at {SIG}")
    raw = json.loads(SIG.read_text())
    bad_kind = {m: r.get("signature_kind") for m, r in raw.items()
                if r.get("signature_kind") != "operator_rates_v2"}
    if bad_kind:
        sys.exit(f"these cells are not on operator_rates_v2 and cannot be used "
                 f"(v1 has no parallel_speedup axis and under-predicted a CPU-constrained "
                 f"machine by 64%): {bad_kind}\n"
                 f"fix with: python3 scripts/collection/container_grid.py --probe-only")
    sigs = {m: r["rates_tuples_per_s"] for m, r in raw.items()
            if not r.get("invalid_probes")}
    speedups = {m: (r.get("parallel_speedup") or 1.0) for m, r in raw.items()}
    # Cells whose probe exceeded the repeatability gate are NOT dropped here. At tight CPU
    # quotas the variance of parallel speedup is the physics, not an instrument fault --
    # 3 workers on 2 cores really do schedule unevenly -- and silently discarding them
    # would bias the grid toward the roomy cells. Instead the evaluation is reported twice:
    # over every cell, and over the reliable subset. The PRODUCT path still refuses them
    # (cetp.cross_env.load_rates), because a user asking for one prediction should not be
    # handed a contended measurement.
    unreliable = sorted(m for m, r in raw.items() if r.get("reliable") is False)
    speedup_cv = {m: r.get("parallel_speedup_cv") for m, r in raw.items()}
    reliable = sorted(m for m in sigs if m not in unreliable)
    cells = load_grid()
    machines = sorted(cells["machine_id"].unique())
    print(f"grid: {len(machines)} configurations, {cells['query_id'].nunique()} queries, "
          f"{len(cells)} cells")
    print(f"signatures: {len(sigs)} valid ({sorted(sigs)})")
    print("parallel speedup per cell: "
          + ", ".join(f"{m}={speedups[m]:.2f}x" for m in sorted(speedups)))

    agree = plan_agreement(load_grid.raw)
    print(f"plan agreement: {agree['n_queries_with_differing_plans']}/"
          f"{agree['n_queries']} queries differ across cells"
          + ("  -> the grid isolates the capability term, and cannot test move 3"
             if agree["isolates_capability_term"] else ""))

    pairs, skipped = build_pairs(cells, sigs, speedups)
    if pairs.empty:
        sys.exit(f"no usable pairs (skipped: {skipped})")
    print(f"pairs: {len(pairs)}   skipped: {skipped}")

    if unreliable:
        print(f"cells flagged unreliable (kept, but reported separately): {unreliable}")
        print("  parallel speedup cv: "
              + ", ".join(f"{m}={speedup_cv[m]:.3f}" for m in unreliable
                          if speedup_cv.get(m) is not None))

    results = {p: score(pairs["actual_scaling"].to_numpy(), pairs[p].to_numpy())
               for p in PREDICTORS}
    print(f"\n{'predictor':12s} {'MAPE':>7s} {'MdAPE':>7s} {'q50':>6s} {'q95':>6s}")
    for p in PREDICTORS:
        s = results[p]
        print(f"{p:12s} {s['mape']:7.2f} {s['mdape']:7.2f} "
              f"{s['qerror_median']:6.3f} {s['qerror_p95']:6.3f}")

    # The same table over the reliable subset only, so neither inclusion nor exclusion of
    # the noisy cells is a hidden choice.
    rel_pairs = pairs[pairs["dev"].isin(reliable) & pairs["prod"].isin(reliable)]
    results_reliable = None
    if len(rel_pairs) >= 20:
        results_reliable = {p: score(rel_pairs["actual_scaling"].to_numpy(),
                                     rel_pairs[p].to_numpy()) for p in PREDICTORS}
        print(f"\nreliable-subset only ({len(reliable)} cells, {len(rel_pairs)} pairs):")
        for p in PREDICTORS:
            t = results_reliable[p]
            print(f"{p:16s} {t['mape']:7.2f} {t['mdape']:7.2f}")
    else:
        print(f"\nreliable subset too small to score separately "
              f"({len(reliable)} cells, {len(rel_pairs)} pairs)")

    boots = {
        "naive_minus_operator": cluster_bootstrap(pairs, "naive", "operator"),
        "scalar_seq_minus_operator": cluster_bootstrap(pairs, "scalar_seq", "operator"),
        "scalar_geo_minus_operator": cluster_bootstrap(pairs, "scalar_geo", "operator"),
        # The two tests that decide whether each move earns its keep:
        # does the parallelism axis matter, and does per-operator weighting matter once
        # the baseline has that axis too?
        "operator_serial_minus_operator":
            cluster_bootstrap(pairs, "operator_serial", "operator"),
        "scalar_geo_par_minus_operator":
            cluster_bootstrap(pairs, "scalar_geo_par", "operator"),
        # If this CI spans zero, per-operator RATE weighting adds nothing beyond
        # localising the parallelism correction, and only the latter should be claimed.
        "scalar_localized_par_minus_operator":
            cluster_bootstrap(pairs, "scalar_localized_par", "operator"),
        "scalar_geo_par_minus_scalar_localized_par":
            cluster_bootstrap(pairs, "scalar_geo_par", "scalar_localized_par"),
    }
    print("\npaired cluster bootstrap (positive => operator model is better):")
    for k, b in boots.items():
        print(f"  {k:30s} {b['point_pp']:+7.2f}pp  CI [{b['ci_low_pp']:+.2f}, "
              f"{b['ci_high_pp']:+.2f}]  {'SIGNIFICANT' if b['excludes_zero'] else 'ns'}")

    disp = axis_dispersion(sigs, speedups)
    print("\nhow much each axis varies across the grid "
          "(bounds what the grid can discriminate):")
    for a in AXES + ["parallel_speedup"]:
        if a in disp:
            d = disp[a]
            print(f"  {a:18s} spread {d['spread']:5.2f}x   "
                  f"cv across cells {d['cv_across_cells']:.3f}")
    print(f"  -> this grid varies mainly {disp['_verdict']['grid_varies_mainly']}")

    coll = axis_collinearity(sigs)
    print(f"\noperator signature dimensionality: PC1 {coll['pc1_share']:.1%}, "
          f"mean |r| {coll['mean_abs_offdiagonal_r']:.3f}, "
          f"effective rank (95%) {coll['effective_rank_95pct']}")
    print("  (the old 2-scalar signature: PC1 97.9%, r = +0.958, effective rank 1)")

    curve = few_shot_curve(pairs)
    print("\nfew-shot calibration, leave-one-machine-out, scored on unrevealed queries:")
    print(f"  {'k':>3s} {'spread':>9s} {'random':>9s}")
    by_k = {}
    for row in curve:
        by_k.setdefault(row["k"], {})[row["rule"]] = row["mape"]
    for k in sorted(by_k):
        sp = by_k[k].get("spread")
        rd = by_k[k].get("random")
        print(f"  {k:3d} {sp:9.2f} {(f'{rd:9.2f}' if rd is not None else '        -')}")

    payload = {
        "grid": {"machines": machines, "n_queries": int(cells["query_id"].nunique()),
                 "n_pairs": int(len(pairs)), "skipped": skipped,
                 "warmup_reps_discarded": DISCARD_WARMUP_REPS},
        "predictors": results,
        "predictors_reliable_subset": results_reliable,
        "reliable_cells": reliable,
        "unreliable_cells": unreliable,
        "parallel_speedup_cv": speedup_cv,
        "bootstrap": boots,
        "signature_dimensionality": coll,
        "axis_dispersion": disp,
        "plan_agreement": agree,
        "few_shot": curve,
        "parallel_pairs_frac": float(pairs["parallel"].mean()),
        "parallel_speedup": speedups,
        "mean_unmapped_frac": float(pairs["unmapped_frac"].mean()),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "operator_model_metrics.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n")
    pairs.to_csv(RESULTS / "operator_model_pairs.csv", index=False)
    print(f"\nwrote {RESULTS / 'operator_model_metrics.json'}")
    print(f"wrote {RESULTS / 'operator_model_pairs.csv'}")


if __name__ == "__main__":
    main()
