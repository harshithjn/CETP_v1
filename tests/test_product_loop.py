"""
Tests for the product loop: ingestion, calibration state, and the cross-architecture guard.

Each test pins behaviour that was established by measurement, not by preference:

  the cross-architecture refusal   uncalibrated cross-ISA was measured at ~68.9% MAPE
      against ~21.7% for assuming production equals development, replicated on four
      independent x86 machines. It is refused rather than warned about.
  Query Text redaction             auto_explain emits the statement with every literal it
      contained. The redactor passed that field through untouched until cetp.ingest
      exercised it.
  median calibration               one arbitrary observation made the predictor WORSE than
      no calibration (19.51% against 15.84%), so the correction is a median and is marked
      provisional below 3 observations.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from cetp import cross_env                                          # noqa: E402
from cetp.calibration_store import MIN_OBSERVATIONS, CalibrationStore  # noqa: E402
from cetp.ingest import parse_auto_explain_log, rank_queries        # noqa: E402
from cetp.redact import assert_clean, redact_plan                   # noqa: E402

RATES = {"seqscan": 1e7, "hashjoin": 6e6, "sort": 5e6, "hashagg": 3e6, "nestloop": 1e6}
ARM = {"client_platform": "Darwin arm64"}
X86 = {"client_platform": "Linux x86_64"}

PLAN = {
    "Query Text": "select * from orders where o_custkey = 42 and o_comment = 'secret'",
    "Plan": {"Node Type": "Seq Scan", "Relation Name": "orders",
             "Filter": "((o_custkey = 42) AND (o_comment = 'secret'::text))",
             "Actual Total Time": 12.5, "Actual Loops": 1,
             "Actual Rows": 3, "Plan Rows": 3},
    "Execution Time": 12.9,
}


def _plan():
    return {"Execution Time": 100.0,
            "Plan": {"Node Type": "Seq Scan", "Actual Total Time": 100.0,
                     "Actual Loops": 1, "Actual Rows": 1, "Plan Rows": 1}}


# ------------------------------------------------- cross-architecture guard

def test_cross_arch_uncalibrated_is_refused():
    with pytest.raises(cross_env.CrossArchitectureUncalibrated):
        cross_env.predict(None, _plan(), 100.0, RATES, RATES,
                          plan_forced=True, dev_rec=ARM, prod_rec=X86)


def test_cross_arch_with_calibration_is_allowed_but_warned():
    out = cross_env.predict(None, _plan(), 100.0, RATES, RATES, plan_forced=True,
                            calibration=0.6, dev_rec=ARM, prod_rec=X86)
    assert out["cross_architecture"] is True
    assert out["trust"] == "MEDIUM"          # never HIGH across an ISA
    assert any("CROSS-ARCHITECTURE" in w for w in out["warnings"])


def test_cross_arch_override_is_possible_but_low_trust():
    out = cross_env.predict(None, _plan(), 100.0, RATES, RATES, plan_forced=True,
                            dev_rec=ARM, prod_rec=X86,
                            allow_uncalibrated_cross_arch=True)
    assert out["trust"] == "LOW"


def test_same_arch_uncalibrated_is_not_refused():
    out = cross_env.predict(None, _plan(), 100.0, RATES, RATES,
                            plan_forced=True, dev_rec=ARM, prod_rec=ARM)
    assert out["cross_architecture"] is False


def test_missing_platform_info_does_not_trigger_refusal():
    """Absence of architecture metadata must not be read as a mismatch."""
    out = cross_env.predict(None, _plan(), 100.0, RATES, RATES, plan_forced=True,
                            dev_rec={}, prod_rec={})
    assert out["cross_architecture"] is False


@pytest.mark.parametrize("a,b,expected", [
    ("Darwin arm64", "Linux x86_64", True),
    ("Linux x86_64", "Linux x86_64", False),
    ("Darwin arm64", "Darwin arm64", False),
])
def test_architectures_differ(a, b, expected):
    differ, _, _ = cross_env.architectures_differ({"client_platform": a},
                                                  {"client_platform": b})
    assert differ is expected


# ------------------------------------------------- auto_explain ingestion

def _log(prefixed=True, tabs=False):
    body = json.dumps(PLAN, indent=2)
    head = "2026-10-13 06:00:00 UTC [1] LOG:  duration: 412.880 ms  plan:"
    if tabs:
        return head + "\n" + "\n".join("\t" + l for l in body.split("\n"))
    if prefixed:
        return head + "\n" + "\n".join(
            "2026-10-13 06:00:00 UTC [1] DETAIL:  " + l for l in body.split("\n"))
    return head + "\n" + body


@pytest.mark.parametrize("kw", [{"prefixed": True}, {"prefixed": False}, {"tabs": True}])
def test_auto_explain_parses_every_log_layout(kw):
    """
    A log_line_prefix repeats on every line of a multi-line message, so the prefixes land
    INSIDE the JSON. The first parser broke on exactly this, which is how real
    auto_explain output looks.
    """
    got, dropped = parse_auto_explain_log(_log(**kw))
    assert len(got) == 1 and not dropped
    assert got[0]["duration_ms"] == pytest.approx(412.880)


def test_ingested_plans_are_redacted_before_being_returned():
    got, _ = parse_auto_explain_log(_log())
    blob = json.dumps(got[0]["plan"])
    assert "secret" not in blob
    assert "= 42" not in blob
    assert "orders" in blob                  # structure survives at structural level


def test_query_text_literals_are_stripped():
    """auto_explain's Query Text is the most sensitive field in the document."""
    r = redact_plan(PLAN)
    assert_clean(r)
    assert "secret" not in r["Query Text"]
    assert "42" not in r["Query Text"]


def test_query_text_is_dropped_entirely_at_full_level():
    r = redact_plan(PLAN, level="full")
    assert r["Query Text"] == "[redacted]"
    assert_clean(r, level="full")


def test_unparseable_entry_is_dropped_not_raised():
    bad = "LOG:  duration: 1.0 ms  plan:\n{not json at all"
    got, dropped = parse_auto_explain_log(bad, strict=False)
    assert got == [] and len(dropped) <= 1


def test_log_with_no_plans_yields_nothing():
    got, dropped = parse_auto_explain_log("LOG:  connection received\n")
    assert got == [] and dropped == []


# ------------------------------------------------- pg_stat_statements ranking

def test_ranking_is_by_total_time_not_mean():
    """A 5ms query called a million times outranks a nightly 10s report."""
    rows = [{"query_id": "hot", "calls": 1_000_000, "total_ms": 5_000_000, "mean_ms": 5},
            {"query_id": "slow", "calls": 10, "total_ms": 100_000, "mean_ms": 10_000}]
    ranked = rank_queries(rows)
    assert ranked[0]["query_id"] == "hot"
    assert ranked[0]["share_of_total_time"] > ranked[1]["share_of_total_time"]


def test_ranking_marks_a_calibration_budget():
    rows = [{"query_id": str(i), "calls": 1, "total_ms": 100 - i} for i in range(10)]
    ranked = rank_queries(rows)
    assert sum(r["calibration_candidate"] for r in ranked) == MIN_OBSERVATIONS


def test_cumulative_share_reaches_one():
    rows = [{"query_id": str(i), "calls": 1, "total_ms": 10} for i in range(5)]
    assert rank_queries(rows)[-1]["cumulative_share"] == pytest.approx(1.0)


# ------------------------------------------------- calibration store

def test_store_is_empty_and_harmless_before_anything_is_recorded(tmp_path):
    c = CalibrationStore(tmp_path / "c.json").calibration("a", "b")
    assert c["factor"] == 1.0 and c["n"] == 0 and c["provisional"] is True


def test_store_uses_a_median_so_one_outlier_cannot_drag_it(tmp_path):
    st = CalibrationStore(tmp_path / "c.json")
    for q, a in [("q1", 2.0), ("q2", 2.1), ("q3", 1.9), ("q4", 50.0)]:
        st.record("d", "p", q, 1.0, a)
    assert st.calibration("d", "p")["factor"] == pytest.approx(2.05)


def test_store_marks_small_samples_provisional(tmp_path):
    st = CalibrationStore(tmp_path / "c.json")
    for i in range(MIN_OBSERVATIONS - 1):
        st.record("d", "p", f"q{i}", 1.0, 2.0)
    assert st.calibration("d", "p")["provisional"] is True
    st.record("d", "p", "qN", 1.0, 2.0)
    assert st.calibration("d", "p")["provisional"] is False


def test_remeasuring_a_query_supersedes_its_old_value(tmp_path):
    st = CalibrationStore(tmp_path / "c.json")
    st.record("d", "p", "q1", 1.0, 5.0)
    st.record("d", "p", "q1", 1.0, 2.0)
    assert st.calibration("d", "p")["n"] == 1
    assert st.calibration("d", "p")["factor"] == pytest.approx(2.0)


def test_store_round_trips_through_disk(tmp_path):
    path = tmp_path / "c.json"
    CalibrationStore(path).record("d", "p", "q1", 1.0, 3.0).save()
    assert CalibrationStore(path).calibration("d", "p")["factor"] == pytest.approx(3.0)


def test_corrupt_store_degrades_to_zero_shot_rather_than_crashing(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{ this is not json")
    assert CalibrationStore(path).calibration("d", "p")["factor"] == 1.0


def test_pairs_are_kept_separate(tmp_path):
    st = CalibrationStore(tmp_path / "c.json")
    st.record("d", "p1", "q1", 1.0, 2.0)
    st.record("d", "p2", "q1", 1.0, 9.0)
    assert st.calibration("d", "p1")["factor"] == pytest.approx(2.0)
    assert st.calibration("d", "p2")["factor"] == pytest.approx(9.0)


def test_learning_curve_shows_the_correction_converging(tmp_path):
    st = CalibrationStore(tmp_path / "c.json")
    for i, a in enumerate([0.62, 0.58, 0.61, 0.60, 0.59]):
        st.record("d", "p", f"q{i}", 1.0, a)
    curve = st.learning_curve("d", "p")
    assert [c["n"] for c in curve] == [1, 2, 3, 4, 5]
    assert curve[-1]["provisional"] is False
    assert curve[-1]["factor"] == pytest.approx(0.60, abs=0.02)


def test_non_positive_observations_are_rejected(tmp_path):
    st = CalibrationStore(tmp_path / "c.json")
    with pytest.raises(ValueError):
        st.record("d", "p", "q", 0.0, 1.0)
    with pytest.raises(ValueError):
        st.record("d", "p", "q", 1.0, -1.0)
