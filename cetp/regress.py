"""
Regression detection (Tier 4) -- the releasable mode.

Absolute prediction cannot gate an SLA at this accuracy. Measured: 54.5% of BLOCK verdicts
were false alarms, and a conformal gate can never BLOCK at all because the intervals are too
wide. So the product should not sell absolute latency.

What it can sell is a RATIO. Ask not "how long will q7 take in production" but "does this
change make q7 slower in production, and by how much". The arithmetic is why this works:

    predicted_prod_after    dev_after x s_after      dev_after     s_after
    --------------------- = ------------------- =   ----------- x  -------
    predicted_prod_before   dev_before x s_before    dev_before    s_before

The hardware term appears in both numerator and denominator. When the two plans have similar
capability profiles, s_after / s_before is close to 1 and **the hardware term cancels** --
the predicted production regression is just the measured dev regression, which is directly
observed rather than modelled. The prediction error that limits absolute mode largely
divides out.

`hardware_term` reports exactly how much did not cancel. When it is near 1.0 the result
carries the confidence of a direct measurement; when it is far from 1.0 the change altered
the plan's resource profile and the hardware term is doing real work, so trust it less.
"""

from . import predict as _p
from . import signature as sig


def detect(before_json, after_json, dev_signature, prod_signature,
           predictor=_p.DEFAULT_PREDICTOR, calibration=None, threshold=1.20):
    """Compare two plans for the same query, captured on the same dev machine.

    `threshold` is the ratio above which a regression is reported, default 1.20 (20% slower).
    """
    cal = calibration or _p.load_calibration()
    b = _p.predict(before_json, dev_signature, prod_signature, predictor, cal)
    a = _p.predict(after_json, dev_signature, prod_signature, predictor, cal)

    dev_ratio = a["dev_time_ms"] / b["dev_time_ms"]
    prod_ratio = a["predicted_ms"] / b["predicted_ms"]
    hardware_term = a["scaling_factor"] / b["scaling_factor"]

    # How much of the answer is modelled rather than measured.
    cancelled = abs(1.0 - hardware_term) < 0.02
    verdict = ("REGRESSION" if prod_ratio >= threshold else
               "IMPROVEMENT" if prod_ratio <= 1.0 / threshold else "NEUTRAL")

    reason = (f"predicted production runtime changes by {prod_ratio:.2f}x "
              f"({'slower' if prod_ratio > 1 else 'faster'}); "
              f"threshold is {threshold:.2f}x")
    if cancelled:
        trust = ("HIGH: the hardware term cancelled (within 2%), so this ratio is "
                 "essentially the directly measured dev-side ratio and does not depend on "
                 "the cross-machine model being right.")
    elif abs(1.0 - hardware_term) < 0.15:
        trust = (f"MODERATE: the hardware term contributes {hardware_term:.3f}x, so most but "
                 f"not all of the model error cancels.")
    else:
        trust = (f"LOW: the hardware term contributes {hardware_term:.3f}x -- the change "
                 f"altered the plan's resource profile, so the cross-machine model is doing "
                 f"real work here and its ~21.6% error applies.")

    return {
        "verdict": verdict,
        "dev_ratio": dev_ratio,
        "predicted_prod_ratio": prod_ratio,
        "hardware_term": hardware_term,
        "hardware_term_cancelled": cancelled,
        "trust": trust,
        "reason": reason,
        "threshold": threshold,
        "before": {"dev_ms": b["dev_time_ms"], "predicted_ms": b["predicted_ms"],
                   "scaling": b["scaling_factor"], "cost": b["features"]["cost"],
                   "node_count": b["features"]["node_count"]},
        "after": {"dev_ms": a["dev_time_ms"], "predicted_ms": a["predicted_ms"],
                  "scaling": a["scaling_factor"], "cost": a["features"]["cost"],
                  "node_count": a["features"]["node_count"]},
        "confidence": a["confidence"],
        "low_confidence": a["low_confidence"],
        "warnings": a["warnings"],
    }


def format_report(result, query_id="query"):
    r = result
    icon = {"REGRESSION": "REGRESSION", "IMPROVEMENT": "IMPROVEMENT",
            "NEUTRAL": "no change"}[r["verdict"]]
    lines = [
        f"CETP regression check for {query_id}: {icon}",
        f"  dev runtime:        {r['before']['dev_ms']:.1f} ms -> "
        f"{r['after']['dev_ms']:.1f} ms  ({r['dev_ratio']:.2f}x)",
        f"  predicted prod:     {r['before']['predicted_ms']:.1f} ms -> "
        f"{r['after']['predicted_ms']:.1f} ms  ({r['predicted_prod_ratio']:.2f}x)",
        f"  hardware term:      {r['hardware_term']:.3f}x"
        + ("  (cancelled)" if r["hardware_term_cancelled"] else ""),
        f"  plan nodes:         {r['before']['node_count']} -> {r['after']['node_count']}",
        f"  trust:              {r['trust']}",
        f"  reason:             {r['reason']}",
    ]
    if r["low_confidence"]:
        lines.append(f"  confidence:         {r['confidence']:.2f} (LOW)")
    return "\n".join(lines)
