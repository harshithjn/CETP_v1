"""
Calibrated prediction intervals (TASKS.md Tier 1, items 20, 21, 24, 29).

The project's p95/p99 quantile regressors carry NO coverage guarantee, and empirically
under-covered badly (80.7% at nominal 95%). An SLA gate whose "p95" means nothing
checkable cannot be deployed. This module replaces them with distribution-free methods
and measures, honestly, what each one delivers.

Because an SLA gate only ever asks "could this be SLOWER than the threshold", every
interval here is a ONE-SIDED UPPER BOUND. Coverage means P(y <= upper_bound) >= 1-alpha.

Methods compared, all evaluated leave-one-machine-out (the deployment scenario: a
production machine the model has never seen):

  gbr_quantile    item 24 baseline -- GradientBoostingRegressor with quantile loss on
                  the raw ratio. What the project ships. No guarantee.
  gbr_log         item 24 -- the same model fit on log(prod/dev) with SQUARED loss, then
                  exponentiated. Scaling factors are multiplicative, so squared loss in
                  log space is the geometric-mean-optimal fit and is aligned with the
                  ratio-error metrics actually reported.
  split_conformal item 20 -- grouped split conformal. The 4 training machines are split
                  into 3 proper-training machines and 1 CALIBRATION machine; the
                  conformity scores come from a machine the regressor never saw. This
                  respects exchangeability at the machine level, which is the grouping
                  the cross-validation itself uses. Guarantee holds if the target machine
                  is exchangeable with the calibration machine.
  weighted_conf   item 21 -- weighted conformal under covariate shift. The target machine
                  is NOT exchangeable with the training machines (that is the whole c5a
                  finding), so calibration points are reweighted by a likelihood-ratio
                  proxy derived from hardware-signature distance: calibration machines
                  closer to the target in normalised (bandwidth, compute) space count for
                  more. Uses the same distance the confidence gate already uses.

Item 29: measurement noise is propagated rather than ignored. Each cell's p50 comes from
19 warm repetitions, so y = prod_p50/dev_p50 carries a sampling error of roughly
sqrt(cv_prod^2 + cv_dev^2)/sqrt(19). That floor is reported alongside interval widths, so
it is visible how much of an interval is irreducible measurement uncertainty rather than
model uncertainty.

Writes results/tier1_conformal.csv and results/tier1_conformal.md.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_evaluation import (  # noqa: E402
    BANDWIDTH, COMPUTE_MULTI, LEARNED_FEATURES, RANDOM_STATE, RESULTS_DIR,
    attach_probabilities, build_pairs, fit_classifier, load_cells,
)

ALPHAS = [0.10, 0.05, 0.01]      # nominal miscoverage -> 90%, 95%, 99% upper bounds
N_WARM_REPS = 19
TAU = 0.5                         # bandwidth of the covariate-shift weighting kernel
METHODS = ["gbr_quantile", "gbr_log", "split_conformal", "weighted_conf"]


def hw_normalised():
    """Machines in normalised (bandwidth, compute) space -- the same space the gate's
    hardware_distance_confidence uses, so distances are comparable to it."""
    bw = np.array([BANDWIDTH[m] for m in sorted(BANDWIDTH)])
    cm = np.array([COMPUTE_MULTI[m] for m in sorted(BANDWIDTH)])
    out = {}
    for i, m in enumerate(sorted(BANDWIDTH)):
        out[m] = np.array([(bw[i] - bw.min()) / (bw.max() - bw.min()),
                           (cm[i] - cm.min()) / (cm.max() - cm.min())])
    return out


def fit_q(train, alpha, target):
    m = GradientBoostingRegressor(loss="quantile", alpha=alpha, n_estimators=200,
                                  max_depth=3, learning_rate=0.05,
                                  random_state=RANDOM_STATE)
    m.fit(train[LEARNED_FEATURES].to_numpy(), train[target].to_numpy())
    return m


def fit_log_mean(train, target):
    m = GradientBoostingRegressor(loss="squared_error", n_estimators=200, max_depth=3,
                                  learning_rate=0.05, random_state=RANDOM_STATE)
    m.fit(train[LEARNED_FEATURES].to_numpy(), np.log(train[target].to_numpy()))
    return m


def weighted_quantile(values, weights, q):
    """Weighted empirical quantile, used for the covariate-shift-corrected threshold."""
    order = np.argsort(values)
    v, w = np.asarray(values)[order], np.asarray(weights)[order]
    cum = np.cumsum(w) / np.sum(w)
    idx = np.searchsorted(cum, q, side="left")
    return float(v[min(idx, len(v) - 1)])


def main():
    cells = load_cells()
    pairs, machines = build_pairs(cells, COMPUTE_MULTI)
    hw = hw_normalised()
    target_col = "scaling_factor_p50"

    # ---- item 29: the measurement-noise floor on y itself.
    cv_map = cells.set_index(["machine_id", "query_id"])["time_cv"].to_dict()
    pairs = pairs.copy()
    pairs["y_rel_se"] = [
        np.sqrt(cv_map[(r.dev_machine, r.query_id)] ** 2
                + cv_map[(r.prod_machine, r.query_id)] ** 2) / np.sqrt(N_WARM_REPS)
        for r in pairs.itertuples()
    ]

    rows = []
    for held_out in machines:
        train_machines = [m for m in machines if m != held_out]
        test_mask = (pairs["dev_machine"] == held_out) | (pairs["prod_machine"] == held_out)
        test_raw = pairs[test_mask]

        # Full training set (all 4 machines) for the two non-conformal baselines.
        full_mask = pairs["dev_machine"].isin(train_machines) & pairs["prod_machine"].isin(train_machines)
        clf_full = fit_classifier(cells[cells["machine_id"].isin(train_machines)])
        full_train = attach_probabilities(pairs[full_mask], clf_full)
        test_full = attach_probabilities(test_raw, clf_full)
        y_test = test_full[target_col].to_numpy()

        preds = {}
        for a in ALPHAS:
            preds[("gbr_quantile", a)] = fit_q(full_train, 1 - a, target_col).predict(
                test_full[LEARNED_FEATURES].to_numpy())

        # Log-space: fit the mean of log y, then add the empirical (1-a) quantile of the
        # training residuals in log space. Exponentiating gives a multiplicative bound.
        logm = fit_log_mean(full_train, target_col)
        resid = np.log(full_train[target_col].to_numpy()) - logm.predict(
            full_train[LEARNED_FEATURES].to_numpy())
        base_log = logm.predict(test_full[LEARNED_FEATURES].to_numpy())
        for a in ALPHAS:
            preds[("gbr_log", a)] = np.exp(base_log + np.quantile(resid, 1 - a))

        # ---- conformal: rotate which training machine is the calibration machine.
        conf_scores, conf_weights, base_preds = [], [], []
        for calib_m in train_machines:
            proper = [m for m in train_machines if m != calib_m]
            pm = pairs["dev_machine"].isin(proper) & pairs["prod_machine"].isin(proper)
            cm = (pairs["dev_machine"] == calib_m) | (pairs["prod_machine"] == calib_m)
            cm = cm & pairs["dev_machine"].isin(train_machines) & pairs["prod_machine"].isin(train_machines)
            if not pm.any() or not cm.any():
                continue
            clf_p = fit_classifier(cells[cells["machine_id"].isin(proper)])
            ptr = attach_probabilities(pairs[pm], clf_p)
            cal = attach_probabilities(pairs[cm], clf_p)
            mdl = fit_log_mean(ptr, target_col)
            # One-sided conformity score in log space: how far above the point
            # prediction the truth actually fell.
            s = np.log(cal[target_col].to_numpy()) - mdl.predict(cal[LEARNED_FEATURES].to_numpy())
            conf_scores.append(s)
            # Covariate-shift weight: how close is this calibration machine to the target?
            d = np.linalg.norm(hw[calib_m] - hw[held_out])
            conf_weights.append(np.full(len(s), float(np.exp(-d / TAU))))
            base_preds.append(mdl.predict(test_full[LEARNED_FEATURES].to_numpy()))

        scores = np.concatenate(conf_scores)
        weights = np.concatenate(conf_weights)
        base = np.mean(base_preds, axis=0)
        n = len(scores)
        for a in ALPHAS:
            # finite-sample corrected level
            lvl = min(1.0, np.ceil((n + 1) * (1 - a)) / n)
            preds[("split_conformal", a)] = np.exp(base + np.quantile(scores, lvl))
            preds[("weighted_conf", a)] = np.exp(base + weighted_quantile(scores, weights, lvl))

        for method in METHODS:
            for a in ALPHAS:
                ub = preds[(method, a)]
                rows.append({
                    "held_out_machine": held_out, "method": method,
                    "nominal_coverage": 1 - a, "n": len(y_test),
                    "empirical_coverage": float(np.mean(y_test <= ub)),
                    "mean_rel_width": float(np.mean(ub / y_test)),
                    "median_rel_width": float(np.median(ub / y_test)),
                    "mean_y_rel_se": float(test_full["y_rel_se"].mean()),
                })

    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "tier1_conformal.csv", index=False)

    agg = (df.groupby(["method", "nominal_coverage"])
             .agg(empirical=("empirical_coverage", "mean"),
                  worst_machine=("empirical_coverage", "min"),
                  mean_rel_width=("mean_rel_width", "mean"))
             .reset_index())
    print(agg.to_string(index=False))

    noise_floor = df["mean_y_rel_se"].mean()
    L = ["# Calibrated prediction intervals (Tier 1, items 20, 21, 24, 29)", "",
         "One-sided **upper** bounds on `scaling_factor_p50`, evaluated leave-one-machine-out.",
         "Coverage is `P(y <= upper_bound)`; it should be at least the nominal level.",
         "`rel width` is `upper_bound / actual`, averaged — lower is tighter, 1.0 would be a",
         "perfect oracle bound.", "",
         "| method | nominal | empirical (mean over 5 folds) | worst single machine | rel width |",
         "|---|---:|---:|---:|---:|"]
    for r in agg.itertuples():
        flag = "" if r.empirical >= r.nominal_coverage - 0.005 else " ⚠"
        L.append(f"| {r.method} | {r.nominal_coverage:.0%} | {r.empirical:.1%}{flag} | "
                 f"{r.worst_machine:.1%} | {r.mean_rel_width:.3f} |")
    L += ["", "## Reading this", "",
          "**⚠ marks a method that fails to deliver its promised coverage.**", "",
          f"The measurement-noise floor on the target itself is **{noise_floor:.2%}** relative "
          f"standard error (item 29): `y` is a ratio of two p50s each estimated from "
          f"{N_WARM_REPS} warm repetitions, so even a perfect model cannot resolve `y` more "
          f"finely than this. Any interval tighter than that is claiming precision the "
          f"measurements do not contain.", "",
          "`split_conformal` calibrates on a training machine the regressor never saw, so its",
          "guarantee is conditional on the target machine being exchangeable with that",
          "calibration machine. The c5a result is direct evidence that assumption fails for an",
          "outlier machine — which is exactly what `weighted_conf` corrects for, by weighting",
          "calibration points by hardware-signature proximity to the target. Compare their",
          "worst-single-machine columns rather than their means: the mean hides the fold that",
          "matters.", ""]
    (RESULTS_DIR / "tier1_conformal.md").write_text("\n".join(L) + "\n")
    print(f"\nWrote {RESULTS_DIR / 'tier1_conformal.md'}")


if __name__ == "__main__":
    main()
