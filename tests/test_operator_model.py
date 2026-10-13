"""
Tests for the per-operator cross-environment model.

Each test here pins down a bug that was found by RUNNING the model, not by reading it.
The two that matter most:

  test_under_gather_not_parallel_aware   keying the parallelism correction on
      `Parallel Aware` instead of "below a Gather" corrected 46% of q19's plan time
      instead of 100%, predicting 1.49x where the truth was 3.01x.
  test_v1_signature_is_refused           a signature without the parallel_speedup axis
      predicted 1.08x for the same 3.01x, so it must be rejected rather than used.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from cetp import cross_env                                        # noqa: E402
from cetp.operator_model import (cardinality_factor, fit_calibration,  # noqa: E402
                                 predict_scaling, select_calibration_queries,
                                 weights_from_plan)
from cetp.plan_nodes import decompose, operator_for               # noqa: E402

RATES = {"seqscan": 10e6, "hashjoin": 6e6, "sort": 5e6, "hashagg": 3e6, "nestloop": 1e6}


def plan(node, exec_ms=100.0):
    return {"Plan": node, "Execution Time": exec_ms}


def n(ntype, att, loops=1, children=(), **kw):
    d = {"Node Type": ntype, "Actual Total Time": att, "Actual Loops": loops,
         "Actual Rows": 100, "Plan Rows": 100}
    if children:
        d["Plans"] = list(children)
    d.update(kw)
    return d


# --------------------------------------------------- cumulative / per-loop corrections

def test_self_time_subtracts_children():
    p = plan(n("Hash Join", 100.0, children=[n("Seq Scan", 40.0), n("Hash", 25.0)]))
    by = {x["node_type"]: x["self_ms"] for x in decompose(p)["nodes"]}
    assert by["Hash Join"] == pytest.approx(35.0)       # 100 - 40 - 25
    assert by["Seq Scan"] == pytest.approx(40.0)


def test_per_loop_time_is_multiplied_by_loops():
    """An inner node of a nested loop reports the AVERAGE of one loop."""
    inner = n("Index Scan", 0.5, loops=200)             # 100 ms in total, not 0.5
    p = plan(n("Nested Loop", 120.0, children=[n("Seq Scan", 10.0), inner]))
    by = {x["node_type"]: x["self_ms"] for x in decompose(p)["nodes"]}
    assert by["Index Scan"] == pytest.approx(100.0)
    assert by["Nested Loop"] == pytest.approx(10.0)     # 120 - 10 - 100


def test_negative_self_time_is_clamped():
    """Averaged parallel children can exceed the parent; that must not go negative."""
    p = plan(n("Gather", 10.0, children=[n("Seq Scan", 30.0, loops=3)]))
    assert all((x["self_ms"] or 0) >= 0 for x in decompose(p)["nodes"])


# ------------------------------------------------------------------ the parallel axis

def test_under_gather_not_parallel_aware():
    """
    Everything below a Gather runs per-participant, whether or not it is Parallel Aware.
    This is the q19 shape: only the Seq Scan is Parallel Aware, but the Nested Loop and
    Index Scan above it also report loops=3.
    """
    p = plan(n("Gather", 96.0, children=[
        n("Nested Loop", 93.0, loops=3, children=[
            n("Seq Scan", 43.0, loops=3, **{"Parallel Aware": True}),
            n("Index Scan", 0.31, loops=485),
        ]),
    ]))
    nodes = {x["node_type"]: x for x in decompose(p)["nodes"]}
    assert nodes["Seq Scan"]["under_gather"] is True
    assert nodes["Nested Loop"]["under_gather"] is True
    assert nodes["Index Scan"]["under_gather"] is True
    assert nodes["Gather"]["under_gather"] is False
    # and Parallel Aware alone would have caught only the Seq Scan
    assert nodes["Nested Loop"]["parallel_aware"] is False


def test_parallel_speedup_changes_the_prediction():
    """A machine that grants no parallelism is slower, and the model must see it."""
    p = plan(n("Gather", 100.0, children=[n("Seq Scan", 90.0, loops=3,
                                            **{"Parallel Aware": True})]))
    same = predict_scaling(p, RATES, RATES, dev_speedup=1.0, prod_speedup=1.0)
    lost = predict_scaling(p, RATES, RATES, dev_speedup=2.5, prod_speedup=1.0)
    assert same["scaling"] == pytest.approx(1.0, abs=1e-9)
    # identical per-worker rates, but prod cannot parallelise => markedly slower
    assert lost["scaling"] > 2.0


def test_weights_sum_to_one():
    p = plan(n("Hash Join", 100.0, children=[n("Seq Scan", 40.0), n("Sort", 30.0)]))
    w, unmapped, _ = weights_from_plan(p)
    assert sum(w.values()) + unmapped == pytest.approx(1.0)


# ------------------------------------------------------------------ operator mapping

@pytest.mark.parametrize("ntype,expected", [
    ("Seq Scan", "seqscan"), ("Hash Join", "hashjoin"), ("Hash", "hashjoin"),
    ("Sort", "sort"), ("Merge Join", "sort"), ("Nested Loop", "nestloop"),
    ("Index Scan", "nestloop"), ("Gather", None), ("Limit", None),
])
def test_operator_mapping(ntype, expected):
    assert operator_for({"Node Type": ntype}) == expected


def test_aggregate_splits_by_strategy():
    assert operator_for({"Node Type": "Aggregate", "Strategy": "Hashed"}) == "hashagg"
    assert operator_for({"Node Type": "Aggregate", "Strategy": "Sorted"}) == "sort"
    assert operator_for({"Node Type": "Aggregate", "Strategy": "Plain"}) == "seqscan"


def test_unmapped_time_is_reported_not_dropped():
    """Control-flow work must land in unmapped, and weights must still close to 1."""
    p = plan(n("Memoize", 100.0, children=[n("Seq Scan", 20.0)]))
    out = predict_scaling(p, RATES, RATES)
    assert out["unmapped_frac"] > 0.5
    assert out["scaling"] == pytest.approx(1.0, abs=1e-9)


# ------------------------------------------------------------------ few-shot calibration

def test_calibration_uses_median_not_mean():
    """One wild observation must not drag the correction; that is why it is a median."""
    obs = [(1.0, 2.0), (1.0, 2.1), (1.0, 1.9), (1.0, 50.0)]
    assert fit_calibration(obs) == pytest.approx(2.05)


def test_calibration_of_nothing_is_identity():
    assert fit_calibration([]) == 1.0


def test_k1_selection_picks_the_median_query():
    """Random k=1 made the predictor WORSE in the cold-start study (19.51 vs 15.84)."""
    cand = {"a": 0.1, "b": 1.0, "c": 9.0}
    assert select_calibration_queries(cand, 1) == ["b"]


def test_selection_spreads_over_the_range():
    cand = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0, "e": 5.0}
    assert set(select_calibration_queries(cand, 3)) == {"a", "c", "e"}


def test_selection_never_repeats_a_query():
    cand = {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0}
    chosen = select_calibration_queries(cand, 3)
    assert len(chosen) == len(set(chosen)) == 3


# ------------------------------------------------------------------ cardinality term

def test_cardinality_identity_at_ratio_one():
    for op in ("seqscan", "sort", "hashjoin", None):
        assert cardinality_factor(op, 1.0) == pytest.approx(1.0)


def test_sort_is_superlinear_in_rows():
    assert cardinality_factor("sort", 4.0) > cardinality_factor("seqscan", 4.0)


def test_sort_superlinearity_is_mild_not_logarithmic_blowup():
    """
    The exact n-log-n ratio is r * (1 + log r / log n), so at r=4 the correction over
    linear is ~1.1x, not ~3x. The first implementation here used r*log2(2r), which gave
    12.0 at r=4 against a true ~4.5 -- a 2.7x over-penalty on every sort.
    """
    assert cardinality_factor("sort", 4.0) == pytest.approx(4.48, abs=0.05)
    assert cardinality_factor("sort", 4.0) < 1.2 * cardinality_factor("seqscan", 4.0)


def test_sort_factor_is_insensitive_to_assumed_n():
    """Only log(n) enters, so a wrong default must not move the answer much."""
    a = cardinality_factor("sort", 4.0, 1e4)
    b = cardinality_factor("sort", 4.0, 1e7)
    assert abs(a - b) / a < 0.06


# ------------------------------------------------------------------ signature guards

def _write_reg(tmp_path, kind, speedup=2.0):
    rec = {"signature_kind": kind, "rates_tuples_per_s": RATES, "invalid_probes": [],
           "parallel_speedup": speedup,
           "probe_scale": {"table_a_rows": 1, "work_mem": "64MB"}}
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"dev": rec, "prod": dict(rec)}))
    return p


def test_v1_signature_is_refused(tmp_path):
    reg = _write_reg(tmp_path, "operator_rates_v1")
    with pytest.raises(ValueError, match="parallel_speedup"):
        cross_env.load_rates(str(reg), "dev")


def test_v2_signature_loads(tmp_path):
    reg = _write_reg(tmp_path, "operator_rates_v2")
    rates, rec = cross_env.load_rates(str(reg), "dev")
    assert rates == RATES and rec["parallel_speedup"] == 2.0


def test_invalid_probes_are_refused(tmp_path):
    reg = json.loads(_write_reg(tmp_path, "operator_rates_v2").read_text())
    reg["dev"]["invalid_probes"] = ["nestloop"]
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(reg))
    with pytest.raises(ValueError, match="invalid probes"):
        cross_env.load_rates(str(p), "dev")


def test_mismatched_probe_scales_are_refused():
    a = {"probe_scale": {"table_a_rows": 400000, "work_mem": "64MB"}}
    b = {"probe_scale": {"table_a_rows": 400000, "work_mem": "4MB"}}
    with pytest.raises(ValueError, match="probe scales differ"):
        cross_env.assert_comparable(a, b)


def test_unreliable_signature_is_refused(tmp_path):
    """A probe taken on a busy machine measures contention, not capability.

    Measured: probing the host while a container sweep ran on it moved seqscan's cv from
    0.008 to 0.337 and the derived parallel speedup from 2.47x to 1.14x.
    """
    reg = json.loads(_write_reg(tmp_path, "operator_rates_v2").read_text())
    reg["dev"]["reliable"] = False
    reg["dev"]["noisy_probes"] = {"hashagg": 0.17}
    p = tmp_path / "noisy.json"
    p.write_text(json.dumps(reg))
    with pytest.raises(ValueError, match="unreliable"):
        cross_env.load_rates(str(p), "dev")


def test_reliable_true_is_accepted(tmp_path):
    reg = json.loads(_write_reg(tmp_path, "operator_rates_v2").read_text())
    reg["dev"]["reliable"] = True
    p = tmp_path / "ok.json"
    p.write_text(json.dumps(reg))
    rates, _ = cross_env.load_rates(str(p), "dev")
    assert rates == RATES


def test_missing_reliable_field_is_tolerated(tmp_path):
    """Older records predate the field; absence must not be read as False."""
    reg = _write_reg(tmp_path, "operator_rates_v2")
    rates, _ = cross_env.load_rates(str(reg), "dev")
    assert rates == RATES


def test_unknown_machine_is_refused(tmp_path):
    reg = _write_reg(tmp_path, "operator_rates_v2")
    with pytest.raises(KeyError):
        cross_env.load_rates(str(reg), "nope")


# ------------------------------------------------------------------ trust / warnings

def test_unforced_plan_is_never_high_trust():
    p = plan(n("Seq Scan", 100.0))
    out = cross_env.predict(None, p, 100.0, RATES, RATES, plan_forced=False)
    assert out["trust"] == "LOW"
    assert any("could NOT be forced" in w for w in out["warnings"])


def test_missing_prod_plan_is_declared():
    p = plan(n("Seq Scan", 100.0))
    out = cross_env.predict(None, p, 100.0, RATES, RATES, plan_forced=None)
    assert out["plan_source"] == "development's own plan"
    assert any("No production plan supplied" in w for w in out["warnings"])


def test_single_client_is_warned_about():
    p = plan(n("Seq Scan", 100.0))
    out = cross_env.predict(None, p, 100.0, RATES, RATES, plan_forced=True)
    assert out["concurrency_factor"] == 1.0
    assert any("SINGLE-CLIENT" in w for w in out["warnings"])


def test_concurrency_factor_is_applied_and_flagged_as_default():
    """T(N) = T(1) * N^e with the measured median e = 0.734 -> 4.60x at 8 clients."""
    p = plan(n("Seq Scan", 100.0))
    out = cross_env.predict(None, p, 100.0, RATES, RATES, plan_forced=True,
                            prod_clients=8)
    assert out["concurrency_factor"] == pytest.approx(8 ** 0.734, rel=1e-6)
    assert out["predicted_prod_ms"] == pytest.approx(100.0 * out["scaling"])
    assert out["concurrency_elasticity_is_default"] is True
    assert any("DEFAULT" in w and "elasticity" in w for w in out["warnings"])


def test_supplied_elasticity_is_not_flagged_as_default():
    p = plan(n("Seq Scan", 100.0))
    out = cross_env.predict(None, p, 100.0, RATES, RATES, plan_forced=True,
                            prod_clients=8, concurrency_elasticity=0.9)
    assert out["concurrency_factor"] == pytest.approx(8 ** 0.9, rel=1e-6)
    assert out["concurrency_elasticity_is_default"] is False


def test_concurrency_at_one_client_is_identity():
    p = plan(n("Seq Scan", 100.0))
    a = cross_env.predict(None, p, 100.0, RATES, RATES, plan_forced=True, prod_clients=1)
    b = cross_env.predict(None, p, 100.0, RATES, RATES, plan_forced=True)
    assert a["scaling"] == pytest.approx(b["scaling"])


def test_forced_plan_can_reach_high_trust():
    """HIGH requires BOTH production's plan and a calibration."""
    p = plan(n("Seq Scan", 100.0))
    out = cross_env.predict(None, p, 100.0, RATES, RATES, plan_forced=True,
                            calibration=1.2)
    assert out["trust"] == "HIGH"


def test_uncalibrated_is_never_high_trust():
    """
    Zero-shot beats naive (28.54% against 38.22%) but carries a systematic per-machine
    over-prediction bias (mean signed error +0.181) that only calibration removes, so an
    uncalibrated prediction caps at MEDIUM however clean the plan side is.
    """
    p = plan(n("Seq Scan", 100.0))
    out = cross_env.predict(None, p, 100.0, RATES, RATES, plan_forced=True,
                            calibration=1.0)
    assert out["trust"] == "MEDIUM"
    assert any("Uncalibrated" in w for w in out["warnings"])
