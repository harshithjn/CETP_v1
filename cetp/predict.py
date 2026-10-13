"""
Cross-environment runtime prediction (Tier 4).

Given an EXPLAIN plan captured on a development machine and the hardware signatures of the
dev and production machines, predict production runtime as a point estimate plus calibrated
upper bounds.

**Ships the analytical formula, not a pickled model.** Measured reasons, leave-one-machine-out:
`1/compute_ratio` 20.06% MAPE, analytical roofline 21.61%, learned GBR 23.90%. The learned
model only interpolates -- 17.29% with every machine seen, 26.86% with the target held out.
The formula is also protocol-invariant and immune to the three-way staleness of
models/*.pkl (see models/MODEL_CARD.md).

Intervals come from models/calibration.json: conformal quantiles of log(actual/predicted)
computed leave-one-machine-out. Coverage is MARGINAL -- on the worst single machine it was
90.5% against a nominal 95% -- so a bound is a calibrated expectation, not a guarantee on an
outlier machine. `confidence` reports how far the production machine sits from the machines
the calibration was built on.
"""

import json
import math
from pathlib import Path

from . import signature as sig

PROJECT_DIR = Path(__file__).resolve().parents[1]
CALIBRATION = PROJECT_DIR / "models" / "calibration.json"
DEFAULT_PREDICTOR = "analytical"
CONFIDENCE_THRESHOLD = 0.6


def load_calibration(path=CALIBRATION):
    if not Path(path).exists():
        raise FileNotFoundError(
            f"{path} missing -- run scripts/analysis/build_calibration.py")
    return json.loads(Path(path).read_text())


def root_buffers(plan, key):
    """Root node only. PostgreSQL reports these cumulatively; summing the tree inflated the
    original dataset 3.06x-12.00x."""
    return plan.get(key, 0) or 0


def features_from_plan(explain_json):
    """Extract the dev-side workload descriptor from an EXPLAIN FORMAT JSON document."""
    doc = explain_json[0] if isinstance(explain_json, list) else explain_json
    plan = doc.get("Plan", doc)
    hit = root_buffers(plan, "Shared Hit Blocks")
    read = root_buffers(plan, "Shared Read Blocks")
    total = hit + read
    return {
        "cost": plan.get("Total Cost"),
        "rows": plan.get("Actual Rows"),
        "shared_hit": hit, "shared_read": read, "total_buffers": total,
        "io_ratio": read / (total + 1),
        "time_ms": doc.get("Execution Time"),
        "node_count": _count(plan),
    }


def _count(node):
    return 1 + sum(_count(c) for c in node.get("Plans", []))


def analytical_scaling(bandwidth_ratio, compute_ratio, weights=None):
    """Roofline-style scaling factor.

    Tier 1 established that the bottleneck-probability weighting contributes nothing: a flat
    uniform split scores 21.55% against 21.61% for a trained classifier, and measured
    resource elasticity scores 21.76%. So the default here is the uniform weighting -- no
    classifier, no pickle, no 21 hand-assigned labels. `weights` allows overriding it.
    """
    inv_bw = 1.0 / bandwidth_ratio
    inv_cmp = 1.0 / compute_ratio
    w = weights or {"bandwidth": 0.5, "compute": 0.25, "mixed": 0.25}
    return (w["bandwidth"] * inv_bw + w["compute"] * inv_cmp
            + w["mixed"] * math.sqrt(inv_bw * inv_cmp))


def compute_only_scaling(bandwidth_ratio, compute_ratio):
    """Best point estimate found: 20.06% MAPE, best on MdAPE and q-error too. Its edge over
    the baseline was NOT statistically significant at 5 machines (CI [-0.09, 6.07])."""
    return 1.0 / compute_ratio


def hardware_distance_confidence(prod, reference):
    """1 - (nearest normalised distance)/sqrt(2) over the calibration machines.

    Note the threshold of 0.6 is a hand-set constant, NOT derived: Tier 1 showed the score
    takes one value per machine, giving only 6 distinct operating points, and that abstaining
    does not reduce error monotonically. It is a coarse outlier flag, nothing more.
    """
    bws = [m["bandwidth"] for m in reference]
    cms = [m["compute"] for m in reference]
    if max(bws) == min(bws) or max(cms) == min(cms):
        return 1.0
    def norm(b, c):
        return ((b - min(bws)) / (max(bws) - min(bws)),
                (c - min(cms)) / (max(cms) - min(cms)))
    px, py = norm(prod["bandwidth"], prod["compute"])
    best = min(math.dist((px, py), norm(m["bandwidth"], m["compute"])) for m in reference)
    return max(0.0, 1.0 - best / math.sqrt(2))


def predict(explain_json, dev_signature, prod_signature, predictor=DEFAULT_PREDICTOR,
            calibration=None, levels=("0.9", "0.95", "0.99")):
    """Predict production runtime from a dev-side plan.

    Returns a dict with the point estimate, calibrated upper bounds, the capability ratios,
    a confidence score, and every warning that applies.
    """
    cal = calibration or load_calibration()
    if predictor not in cal["predictors"]:
        raise ValueError(f"unknown predictor {predictor!r}; "
                         f"have {sorted(cal['predictors'])}")
    feats = features_from_plan(explain_json)
    if not feats["time_ms"]:
        raise ValueError("plan has no Execution Time -- EXPLAIN must be run with ANALYZE")

    r = sig.ratios(dev_signature, prod_signature)
    scaling = (compute_only_scaling(**r) if predictor == "cmp_only"
               else analytical_scaling(**r))
    dev_ms = feats["time_ms"]
    point = dev_ms * scaling

    mult = cal["predictors"][predictor]["multiplier"]
    bounds = {lv: point * mult[lv] for lv in levels if lv in mult}

    reference = [sig.LEGACY[m] | {"machine_id": m} for m in sig.LEGACY]
    conf = hardware_distance_confidence(prod_signature, reference)

    warns = sig.warnings_for(dev_signature, prod_signature)
    if conf < CONFIDENCE_THRESHOLD:
        warns.insert(0, f"LOW CONFIDENCE ({conf:.2f} < {CONFIDENCE_THRESHOLD}): the "
                        f"production signature is an outlier relative to the calibration "
                        f"machines. Measure instead of predicting.")
    return {
        "dev_time_ms": dev_ms,
        "scaling_factor": scaling,
        "predicted_ms": point,
        "bounds_ms": bounds,
        "ratios": r,
        "confidence": conf,
        "low_confidence": conf < CONFIDENCE_THRESHOLD,
        "predictor": predictor,
        "features": feats,
        "warnings": warns,
        "accuracy_note": (
            "Expected error on an unseen machine: ~21.6% MAPE (analytical) or ~20.1% "
            "(cmp_only), measured leave-one-machine-out on 5 EC2 instance types x 21 TPC-H "
            "queries at SF1. Bounds have marginal, not conditional, coverage."),
    }
