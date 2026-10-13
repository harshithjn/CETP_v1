"""
Per-operator cross-environment prediction.

THE MODEL. A single capability ratio cannot say "this machine is fast at scans and slow
at sorts". This one can, because it weights a per-operator capability ratio by where the
development machine actually spent its time:

    scaling  =  SUM over operator axes  w_op * (rate_dev(op) / rate_prod(op)) * card_op
    T_prod   =  T_dev * scaling

    w_op     =  dev self-time in that operator / total dev self-time   (sums to 1)
    rate_*   =  tuples/s from scripts/collection/operator_probe.py
    card_op  =  1.0 when dev and prod see the same data volume (see below)

WHY A WEIGHTED RATIO AND NOT A SUM OF SCALED NODE TIMES. Under a Gather, PostgreSQL
reports each node's `Actual Total Time` averaged across the leader and its workers, so
node self-times sum to total CPU time, not wall time -- measured here at 2.85x wall on a
parallel TPC-H q19. Summing scaled self-times would therefore predict a number ~3x too
large. Normalising to weights makes that factor cancel exactly, because it scales every
node under the Gather by the same constant. The price is that this predicts a RATIO and
needs a measured T_dev, which is what the product has anyway.

UNMAPPED TIME. Control-flow nodes (Gather, Limit, Memoize, ...) do real work that no probe
axis measures. Their weight is scaled by the geometric mean of the mapped ratios, and
`unmapped_frac` is returned on every prediction. A prediction with a large unmapped
fraction is a prediction the model cannot explain, and the caller is expected to say so.

CARDINALITY TERM. When production holds more data than development, the same plan node
processes more rows. `card_op` carries that: linear in the row ratio for scans, joins and
hash aggregation, r*log(r)/... for sorts. This term is IMPLEMENTED BUT NOT VALIDATED --
every cell in the container grid holds identical SF1 data, so it is exercised only at
ratio 1.0 and must not be quoted as a measured capability.
"""

from __future__ import annotations

import math

from .plan_nodes import COMPLEXITY, decompose, operator_profile

AXES = ["seqscan", "hashjoin", "sort", "hashagg", "nestloop"]

# Concurrency. Runtime under N simultaneous clients follows T(N) = T(1) * N^e. This is NOT
# predictable from a single-client plan: leave-one-query-out, plan features scored MAE 0.191
# on e against 0.167 for simply using the median, so the plan carries no usable signal and
# the caller must supply the load. The default below is the measured median across 22 TPC-H
# queries on an 8-core machine, and using it roughly halves the error against ignoring
# concurrency entirely (at 8 clients, 41.8% against 73.8%) -- but 41.8% is still large, and
# the per-query oracle reaches 6.7%, so a query whose elasticity actually matters should be
# measured under load rather than defaulted. See results/concurrency.md.
DEFAULT_CONCURRENCY_ELASTICITY = 0.734
# parallel_seqscan is a probe axis but not a plan-node axis: it exists only to
# derive `parallel_speedup`, which multiplies the others on parallel nodes.
PROBE_ONLY_AXES = ["parallel_seqscan"]


def _geomean(xs):
    xs = [x for x in xs if x and x > 0]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else 1.0


# Rows assumed for an n-log-n node when the plan does not say. Only the LOG of this
# matters, so the factor is insensitive to it: at row_ratio=4 the superlinear correction is
# 1.11x with n=10^4 and 1.07x with n=10^7.
DEFAULT_SORT_ROWS = 1e5


def cardinality_factor(operator, row_ratio, n_rows=None):
    """
    Cost multiplier for processing `row_ratio` times as many rows.

    Linear operators scale with the ratio. For a sort, the exact ratio of n-log-n costs is

        (r*n * log(r*n)) / (n * log n)  =  r * (1 + log(r) / log(n))

    so the superlinear part depends on the node's OWN row count and is modest: at r=4 the
    correction is about 1.1x on top of the 4x, not the 3x a naive r*log2(2r) form gives.
    That naive form was the first implementation here and it over-penalised sorts by about
    2.7x at r=4, which is why the exact expression is used instead.
    """
    if row_ratio is None or row_ratio <= 0:
        return 1.0
    if COMPLEXITY.get(operator) == "nlogn":
        n = n_rows if (n_rows and n_rows > 1) else DEFAULT_SORT_ROWS
        return row_ratio * (1.0 + math.log(row_ratio) / math.log(n))
    return row_ratio


def weights_from_plan(plan, dev_speedup=1.0):
    """
    Normalised dev WALL-time weights per (axis, is_parallel), plus the unmapped share.

    The parallel split is not cosmetic. Under a Gather, `Actual Total Time` is averaged
    across participants, so a parallel node's self-time is a CPU-time share, inflated
    relative to wall time by roughly the speedup the dev machine achieved. Dividing those
    nodes by `dev_speedup` converts them back to wall-time shares, which is what a runtime
    prediction needs.

    Measured consequence of getting this wrong: predicting the 1-CPU container from the
    8-core host with serial-only axes gave 1.29x against a true 3.01x, because the host
    runs 3 workers concurrently and the container timeshares them on one CPU.
    """
    d = decompose(plan)
    buckets, unmapped, rows = {}, 0.0, {}
    for n in d["nodes"]:
        t = n["self_ms"] or 0.0
        if t <= 0:
            continue
        # under_gather, not parallel_aware -- see the note in plan_nodes.decompose.
        par = bool(n.get("under_gather"))
        # CPU-time share -> wall-time share for the parallel part of the plan.
        t_wall = t / dev_speedup if (par and dev_speedup) else t
        if n["operator"] is None:
            unmapped += t_wall
        else:
            key = (n["operator"], par)
            buckets[key] = buckets.get(key, 0.0) + t_wall
            # Keep the largest row count in the bucket: it dominates an n-log-n cost.
            rows[key] = max(rows.get(key, 0.0), n["rows"] or 0.0)
    total = sum(buckets.values()) + unmapped
    if total <= 0:
        return {}, 1.0, {"profile": {}, "unmapped_ms": 0.0, "unmapped_frac": 1.0,
                         "bucket_rows": {}, "decomposition": d}
    w = {k: v / total for k, v in buckets.items()}
    prof = {"profile": {}, "unmapped_ms": unmapped, "bucket_rows": rows,
            "unmapped_frac": unmapped / total, "decomposition": d}
    for (op, _par), v in buckets.items():
        prof["profile"][op] = prof["profile"].get(op, 0.0) + v
    return w, unmapped / total, prof


def concurrency_factor(clients, elasticity=None):
    """Runtime multiplier for running at `clients` simultaneous clients instead of 1."""
    if not clients or clients <= 1:
        return 1.0, None
    e = DEFAULT_CONCURRENCY_ELASTICITY if elasticity is None else float(elasticity)
    return float(clients) ** e, e


def predict_scaling(plan, dev_rates, prod_rates, row_ratio=None, calibration=1.0,
                    dev_speedup=1.0, prod_speedup=1.0,
                    prod_clients=1, concurrency_elasticity=None):
    """
    Returns a dict with the predicted dev->prod scaling factor and everything needed to
    judge whether to believe it.

    plan        dev-side EXPLAIN (ANALYZE) JSON. Ideally the plan PRODUCTION chose, forced
                on dev, so plan divergence contributes nothing (see predict_cross_env).
    dev_rates   operator rates for the dev machine   {axis: tuples/s}
    prod_rates  operator rates for the prod machine
    row_ratio   prod rows / dev rows for the same query, or None when the data is identical
    calibration multiplicative correction for this prod machine, from few-shot calibration
    """
    dev_speedup = dev_speedup or 1.0
    prod_speedup = prod_speedup or 1.0
    w, unmapped_frac, prof = weights_from_plan(plan, dev_speedup)
    missing = sorted({op for (op, _p) in w if op not in dev_rates or op not in prod_rates})
    ratios, contrib, axis_weight = {}, {}, {}
    for (op, par), wt in w.items():
        axis_weight[op] = axis_weight.get(op, 0.0) + wt
        if op in missing:
            continue
        # A parallel node's wall time is per-worker cost divided by the speedup the machine
        # grants, so the dev->prod ratio carries both machines' speedups.
        sd = dev_speedup if par else 1.0
        sp = prod_speedup if par else 1.0
        r = (dev_rates[op] * sd) / (prod_rates[op] * sp)
        c = cardinality_factor(op, row_ratio, prof.get("bucket_rows", {}).get((op, par)))
        ratios[f"{op}{'(par)' if par else ''}"] = r
        contrib[(op, par)] = wt * r * c

    fallback = _geomean(list(ratios.values())) if ratios else 1.0
    # Unmapped work is assumed to scale like the typical mapped operator.
    unmapped_contrib = unmapped_frac * fallback * cardinality_factor(None, row_ratio)
    # Any axis we could not price is folded in the same way rather than dropped, so the
    # weights still sum to 1 and the prediction is not silently biased low.
    missing_w = sum(wt for (op, _p), wt in w.items() if op in missing)
    missing_contrib = missing_w * fallback * cardinality_factor(None, row_ratio)

    scaling = sum(contrib.values()) + unmapped_contrib + missing_contrib
    conc, e_used = concurrency_factor(prod_clients, concurrency_elasticity)
    return {
        "scaling": scaling * calibration * conc,
        "concurrency_factor": conc,
        "concurrency_elasticity_used": e_used,
        "prod_clients": prod_clients,
        "concurrency_elasticity_is_default": (e_used is not None
                                              and concurrency_elasticity is None),
        "scaling_uncalibrated": scaling,
        "calibration": calibration,
        "weights": axis_weight,
        "weights_by_parallelism": {f"{op}{'(par)' if par else ''}": wt
                                   for (op, par), wt in w.items()},
        "per_axis_ratio": ratios,
        "dev_speedup": dev_speedup,
        "prod_speedup": prod_speedup,
        "unmapped_frac": unmapped_frac,
        "unpriced_axes": missing,
        "unpriced_weight": missing_w,
        "explained_frac": sum(wt for (op, _p), wt in w.items() if op not in missing),
        "row_ratio": row_ratio,
        "residual_frac": prof["decomposition"]["residual_frac"],
        "parallel": prof["decomposition"]["parallel"],
    }


def predict_runtime(plan, dev_runtime_ms, dev_rates, prod_rates,
                    row_ratio=None, calibration=1.0,
                    dev_speedup=1.0, prod_speedup=1.0,
                    prod_clients=1, concurrency_elasticity=None):
    out = predict_scaling(plan, dev_rates, prod_rates, row_ratio, calibration,
                          dev_speedup, prod_speedup, prod_clients,
                          concurrency_elasticity)
    out["dev_runtime_ms"] = dev_runtime_ms
    out["predicted_prod_ms"] = dev_runtime_ms * out["scaling"]
    return out


# ----------------------------------------------------------- few-shot calibration

def fit_calibration(observations):
    """
    Estimate one multiplicative correction for a production machine from k measured
    queries. `observations` is [(predicted_scaling, actual_scaling), ...].

    The median of the ratio is used rather than the mean: the cold-start study found that
    calibrating from a single arbitrary query made the predictor WORSE (19.51% against
    15.84% uncalibrated), because one bad ratio drags a mean. The median is what makes
    small k safe.
    """
    rs = [a / p for p, a in observations if p and p > 0 and a and a > 0]
    if not rs:
        return 1.0
    rs.sort()
    n = len(rs)
    return rs[n // 2] if n % 2 else 0.5 * (rs[n // 2 - 1] + rs[n // 2])


def select_calibration_queries(candidates, k):
    """
    Which k queries to measure on a new production machine.

    `candidates` is {query_id: predicted_scaling}. Picks the k whose predicted scalings are
    most evenly spread over the predicted range -- the `spread` strategy, which was the
    most stable column in the cold-start table (12.25-14.41% across k, against `random`
    degrading to 19.51% at k=1). Spread in the space of the quantity being estimated beats
    spread in feature space, which chases outliers as k grows.
    """
    items = sorted(candidates.items(), key=lambda kv: kv[1])
    if k >= len(items):
        return [q for q, _ in items]
    if k == 1:
        return [items[len(items) // 2][0]]          # the median, not a random draw
    lo, hi = items[0][1], items[-1][1]
    targets = [lo + (hi - lo) * i / (k - 1) for i in range(k)]
    chosen, used = [], set()
    for t in targets:
        best = min((abs(v - t), q) for q, v in items if q not in used)
        chosen.append(best[1])
        used.add(best[1])
    return chosen
