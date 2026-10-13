"""
Build the shipped calibration table (Tier 4).

The predictor ships the ANALYTICAL formula rather than a pickled model, for three measured
reasons:

  - Out of hull the formula wins. Leave-one-machine-out, `1/compute_ratio` scores 20.06%
    MAPE and the analytical roofline 21.61%, against 23.90% for the learned GBR
    (results/tier1_ablation.md). The learned model only interpolates: given every machine it
    reaches 17.29%, given a new one it degrades to 26.86%.
  - It needs no pickle, so it is immune to the three-way staleness of models/*.pkl
    (models/MODEL_CARD.md) and to scikit-learn version coupling.
  - Its accuracy is protocol-invariant (21.61 / 21.66 / 21.64% across LOMO / LOQO / LOMQO),
    so it behaves the same on unseen machines and unseen queries.

What a formula cannot give on its own is an INTERVAL. This precomputes the conformal
calibration table: the empirical quantiles of log(actual / predicted), computed
leave-one-machine-out so the quantiles describe performance on a machine the formula was not
tuned for. Shipping these turns a point estimate into a bound with a measured coverage
level, which is what an SLA decision needs.

Writes models/calibration.json.
"""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_evaluation import (  # noqa: E402
    COMPUTE_MULTI, attach_probabilities, build_pairs, fit_classifier, load_cells,
)

PROJECT_DIR = Path(__file__).resolve().parents[2]
OUT = PROJECT_DIR / "models" / "calibration.json"
LEVELS = [0.50, 0.80, 0.90, 0.95, 0.99]


def main():
    cells = load_cells()
    pairs, machines = build_pairs(cells, COMPUTE_MULTI)

    residuals = {"analytical": [], "cmp_only": []}
    folds = {}
    for held in machines:
        clf = fit_classifier(cells[cells["machine_id"] != held])
        te = attach_probabilities(
            pairs[(pairs["dev_machine"] == held) | (pairs["prod_machine"] == held)], clf)
        y = te["scaling_factor_p50"].to_numpy()
        preds = {"analytical": te["analytical_scaling"].to_numpy(),
                 "cmp_only": (1.0 / te["compute_ratio"]).to_numpy()}
        for k, p in preds.items():
            r = np.log(y / p)
            residuals[k].extend(r.tolist())
            folds.setdefault(k, {})[held] = {
                "n": int(len(r)),
                "quantiles": {str(q): float(np.quantile(r, q)) for q in LEVELS},
            }

    table = {
        "schema": "cetp-calibration/1",
        "built_from": "data/corrected/tpch_dataset_corrected_v2.csv",
        "protocol": "leave-one-machine-out; residuals are log(actual / predicted) pooled "
                    "across all 5 held-out-machine folds",
        "n_machines": len(machines),
        "n_pairs": int(len(pairs)),
        "levels": LEVELS,
        "predictors": {},
        "caveats": [
            "Coverage is MARGINAL, not conditional. Pooled across folds it holds; on the "
            "worst single machine it does not (90.5% at nominal 95% in Tier 1). An outlier "
            "machine can breach the stated level.",
            "Calibrated on 5 machines x 21 TPC-H queries at SF1, all EBS-backed, "
            "single-query-at-a-time, PostgreSQL 15. Outside that envelope the quantiles are "
            "extrapolation.",
            "Engine version alone shifts runtime ~30% and changes 12/22 plans "
            "(results/tier2_versions.md). These quantiles do not model it.",
            "Concurrency elasticity ranges +0.286 to +0.984 across queries "
            "(results/tier2_concurrency.md). These quantiles are single-client.",
        ],
    }
    for k, r in residuals.items():
        a = np.asarray(r)
        table["predictors"][k] = {
            "n": int(len(a)),
            "log_residual_quantiles": {str(q): float(np.quantile(a, q)) for q in LEVELS},
            "multiplier": {str(q): float(np.exp(np.quantile(a, q))) for q in LEVELS},
            "per_fold": folds[k],
        }

    OUT.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(PROJECT_DIR)}")
    for k, v in table["predictors"].items():
        m = v["multiplier"]
        print(f"  {k}: p50 x{m['0.5']:.3f}  p90 x{m['0.9']:.3f}  "
              f"p95 x{m['0.95']:.3f}  p99 x{m['0.99']:.3f}  (n={v['n']})")


if __name__ == "__main__":
    main()
