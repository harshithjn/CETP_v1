"""
The product flow: predict production runtime for a query, from the developer's machine.

THE FOUR MOVES, and what each one is for.

  1. CAPABILITY FROM OPERATORS, NOT A PROXY. Both machines are described by
     `operator_probe.py` rates -- tuples/s for seqscan, hashjoin, sort, hashagg, nestloop --
     measured in SQL. The previous signature was FP-vector GFLOPS and STREAM bandwidth,
     which scored 324% MAPE laptop(arm64) -> EC2(x86_64), against 123% for assuming
     production equals development. Nothing in TPC-H issues vector FP instructions; the
     old signature correlated with machine size (r=+0.841 with core count) rather than
     measuring capability. SQL probes also work on managed Postgres, where no shell exists.

  2. PER-OPERATOR SCALING. `operator_model.predict_scaling` weights each axis's capability
     ratio by where development actually spent its time, so the model can express "fast at
     scans, slow at sorts". A single ratio cannot.

  3. THE PLAN COMES FROM PRODUCTION. `EXPLAIN` without ANALYZE on production costs no
     execution -- it plans and returns. That plan is then FORCED on the development machine
     with pg_hint_plan and run with ANALYZE there. Plan divergence is therefore eliminated
     by construction rather than modelled: the causal decomposition measured plan
     divergence at 70.5% of the cross-environment difference (1.673x against 1.145x for
     settings) whenever the plan changed, and this removes that term. Production's row
     estimates also give the data-volume ratio without touching production data.

     If forcing fails -- pg_hint_plan silently ignores hints it cannot express -- the
     prediction is returned with plan_forced=False and the plan-divergence term is live
     again. That is reported, never hidden.

  4. FEW-SHOT, NOT ZERO-SHOT. k well-chosen queries measured on production buy a
     multiplicative correction for that machine. The cold-start study: 15.84% uncalibrated,
     13.20% after ONE well-chosen query, 12.57% at k=3, while k=12 chosen at RANDOM scored
     13.45% and k=1 at random scored 19.51% -- worse than not calibrating. Which queries
     you measure matters more than how many, so selection uses `spread`, not chance.

WHAT THIS STILL DOES NOT MODEL. Concurrency. Every number here is single-client, and
measured concurrency elasticity ranges +0.286 to +0.984 across queries (up to 8.57x
slowdown at 8 clients). Production load is an input the caller must supply; it is not
inferable from a development machine. A prediction from this module is "runtime for this
query on that machine, run alone".
"""

from __future__ import annotations

import json

from .operator_model import (fit_calibration, predict_runtime,
                             select_calibration_queries)
from .plan_nodes import decompose


def load_rates(registry_path, machine_id):
    """Operator rates for a machine, refusing to mix incompatible probe scales."""
    reg = json.loads(open(registry_path).read())
    if machine_id not in reg:
        raise KeyError(f"{machine_id!r} not in {registry_path}; known: {sorted(reg)}")
    rec = reg[machine_id]
    if rec.get("signature_kind") not in ("operator_rates_v1", "operator_rates_v2"):
        raise ValueError(f"{machine_id} carries {rec.get('signature_kind')!r}, "
                         f"not an operator_rates signature")
    if rec.get("signature_kind") == "operator_rates_v1":
        raise ValueError(
            f"{machine_id} carries operator_rates_v1, which has no parallel_speedup axis. "
            f"v1 predicted 1.29x where the true ratio was 3.01x on a CPU-constrained "
            f"machine, because it could not see that parallel workers were timesharing. "
            f"Re-probe it with the current operator_probe.py.")
    if rec.get("invalid_probes"):
        raise ValueError(f"{machine_id} has invalid probes {rec['invalid_probes']}; "
                         f"re-probe it before using it as a capability reference")
    if rec.get("reliable") is False:
        raise ValueError(
            f"{machine_id}'s signature is flagged unreliable "
            f"(noisy probes: {rec.get('noisy_probes')}). The machine was not idle when it "
            f"was probed, so these are contention numbers, not capability numbers -- "
            f"measured directly, contention moved the parallel speedup by 48%. "
            f"Re-probe it on an idle machine.")
    return rec["rates_tuples_per_s"], rec


def assert_comparable(rec_a, rec_b):
    """Two signatures are comparable only at the same probe scale and settings."""
    a, b = rec_a.get("probe_scale", {}), rec_b.get("probe_scale", {})
    diffs = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
    if diffs:
        raise ValueError(f"probe scales differ, rates are not comparable: {diffs}")


def row_ratio_from_plans(prod_plan, dev_plan):
    """
    Data-volume ratio between the two environments, from the plans' root row counts.

    Production contributes PLAN rows (it was never executed, so no actual rows exist);
    development contributes ACTUAL rows. That asymmetry means this is only as good as
    production's cardinality estimates, which is why it is reported separately and
    defaults to None.
    """
    try:
        p = decompose(prod_plan)["nodes"][0]
        d = decompose(dev_plan)["nodes"][0]
        pr = p["plan_rows"] or 0.0
        dr = d["rows"] or d["plan_rows"] or 0.0
        if pr > 0 and dr > 0:
            return pr / dr
    except Exception:
        pass
    return None


def predict(prod_plan, dev_plan_analyzed, dev_runtime_ms, dev_rates, prod_rates,
            plan_forced=None, use_row_ratio=False, calibration=1.0,
            dev_speedup=1.0, prod_speedup=1.0,
            prod_clients=1, concurrency_elasticity=None):
    """
    One cross-environment prediction.

    prod_plan           production's EXPLAIN (no ANALYZE) JSON, or None if unavailable
    dev_plan_analyzed   development's EXPLAIN (ANALYZE) JSON -- ideally production's plan,
                        forced
    plan_forced         True if dev ran production's plan and it verified; False if not;
                        None if no production plan was supplied at all
    """
    rr = row_ratio_from_plans(prod_plan, dev_plan_analyzed) if (use_row_ratio and prod_plan) else None
    out = predict_runtime(dev_plan_analyzed, dev_runtime_ms, dev_rates, prod_rates,
                          row_ratio=rr, calibration=calibration,
                          dev_speedup=dev_speedup, prod_speedup=prod_speedup,
                          prod_clients=prod_clients,
                          concurrency_elasticity=concurrency_elasticity)
    out["plan_forced"] = plan_forced
    out["plan_source"] = ("production, forced on dev" if plan_forced
                          else "production, forcing FAILED" if plan_forced is False
                          else "development's own plan")
    out["trust"] = _trust(out)
    out["warnings"] = _warnings(out)
    return out


def _trust(out):
    if out["explained_frac"] < 0.5:
        return "LOW"
    if out["plan_forced"] is False:
        return "LOW"
    # Zero-shot beats the naive baseline (28.54% against 38.22%) but carries a systematic
    # per-machine over-prediction bias (mean signed error +0.181) that calibration removes,
    # so an uncalibrated prediction caps at MEDIUM rather than HIGH.
    if out["calibration"] == 1.0:
        return "MEDIUM"
    if out["plan_forced"] is None:
        return "MEDIUM"
    if out["unmapped_frac"] > 0.25:
        return "MEDIUM"
    return "HIGH"


def _warnings(out):
    w = []
    if out["plan_forced"] is False:
        w.append("Production's plan could NOT be forced on dev, so plan divergence is "
                 "unaccounted for. The causal decomposition puts that term at 70.5% of "
                 "the cross-environment difference when the plan changes.")
    if out["plan_forced"] is None:
        w.append("No production plan supplied, so this predicts the runtime of DEV's plan "
                 "on production hardware. If production would choose a different plan, "
                 "the dominant term is missing.")
    if out["unmapped_frac"] > 0.1:
        w.append(f"{out['unmapped_frac']:.0%} of dev self-time is in nodes no probe axis "
                 f"measures; that share was scaled by the geometric mean of the others.")
    if out["unpriced_axes"]:
        w.append(f"no probe rate for {out['unpriced_axes']} "
                 f"({out['unpriced_weight']:.0%} of dev time)")
    if out["parallel"]:
        if out["dev_speedup"] == 1.0 and out["prod_speedup"] == 1.0:
            w.append("Plan is parallel but BOTH machines were given a speedup of 1.0, so "
                     "the parallelism term is inert. On a CPU-constrained machine this "
                     "under-predicted 3.01x as 1.29x. Supply parallel_speedup for both.")
        else:
            w.append(f"Plan is parallel; parallel nodes scaled by dev speedup "
                     f"{out['dev_speedup']:.2f}x / prod speedup "
                     f"{out['prod_speedup']:.2f}x.")
    if out["row_ratio"] and abs(out["row_ratio"] - 1.0) > 0.05:
        w.append(f"data volume differs by {out['row_ratio']:.2f}x and the cardinality term "
                 f"is UNVALIDATED -- it was only ever exercised at ratio 1.0.")
    if out["calibration"] == 1.0:
        w.append("Uncalibrated (zero-shot). Across a real environment boundary "
                 "(laptop -> 6 container machines, 132 pairs) zero-shot scored 28.54% MAPE "
                 "against 38.22% for assuming production equals development. Measuring 5-8 "
                 "queries on production takes it to ~23%; measuring only ONE is worse than "
                 "not calibrating (33.74%). See results/laptop_to_prod.md.")
    if out.get("prod_clients", 1) <= 1:
        w.append("SINGLE-CLIENT. No concurrency applied. Production is rarely one client, "
                 "and at 8 clients runtime rose 8.57x on the measured workload -- larger "
                 "than every other term here. Pass prod_clients to account for it.")
    elif out.get("concurrency_elasticity_is_default"):
        w.append(f"Concurrency at {out['prod_clients']} clients applied with the DEFAULT "
                 f"elasticity {out['concurrency_elasticity_used']:.3f} "
                 f"(x{out['concurrency_factor']:.2f}). Elasticity is NOT predictable from a "
                 f"plan (leave-one-query-out MAE 0.191 against 0.167 for the median), so "
                 f"this is a population default, not a property of your query: residual "
                 f"error at 8 clients was 41.8% with the default against 6.7% if the "
                 f"query's own elasticity is known. Measure it under load if it matters.")
    return w


__all__ = ["load_rates", "assert_comparable", "predict", "row_ratio_from_plans",
           "fit_calibration", "select_calibration_queries"]
