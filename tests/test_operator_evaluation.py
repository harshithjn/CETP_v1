"""
Regression tests for scripts/analysis/operator_model_evaluation.py (finding C10).

What went wrong: `results/operator_model_metrics.json` was committed from one probe state
while `data/container_grid/operator_signature_grid.json` was committed in another, and the
script globbed the whole of data/container_grid/, so it later swallowed the four x86 GitHub
Actions cells as well. The committed numbers could not be regenerated, and nothing noticed.

These tests pin the three things that would have caught it:
  * the grid is the manifest's cells, not whatever files happen to be in the directory;
  * the committed metrics record the hash of the inputs they came from, and those inputs
    still match;
  * regenerating from the committed inputs reproduces the committed outputs.
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_DIR / "scripts" / "analysis" / "operator_model_evaluation.py"
COMMITTED = PROJECT_DIR / "results" / "operator_model_metrics.json"
COMMITTED_PAIRS = PROJECT_DIR / "results" / "operator_model_pairs.csv"
PLANS = PROJECT_DIR / "data" / "plans_grid"

spec = importlib.util.spec_from_file_location("operator_model_evaluation", SCRIPT)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

needs_inputs = pytest.mark.skipif(
    not (PLANS.exists() and (ev.GRID_DIR / "grid_manifest.json").exists()),
    reason="data/plans_grid or the grid manifest is not present in this checkout")


# ---------------------------------------------------------------- input selection

@needs_inputs
def test_grid_is_the_manifest_cells_and_excludes_cross_isa_cells():
    ids = ev.grid_machine_ids()
    manifest = json.loads(ev.MANIFEST.read_text())["configs"]
    assert ids == [c["machine_id"] for c in manifest]
    assert len(ids) == 8
    assert not any(i.startswith("gha-") for i in ids)


@needs_inputs
def test_extra_files_in_the_grid_directory_do_not_change_the_grid(tmp_path):
    """The failure itself: a stray tpch_*.csv beside the grid must not be picked up."""
    expected = sorted(c["machine_id"] for c in json.loads(ev.MANIFEST.read_text())["configs"])
    stray = ev.GRID_DIR / "tpch_zz-stray.csv"
    assert not stray.exists()
    stray.write_text("machine_id,query_id,rep,time_ms,ok\nzz-stray,q1,0,1.0,True\n")
    try:
        ids = ev.grid_machine_ids()
        cells = ev.load_grid(ids)
    finally:
        stray.unlink()
    assert sorted(ids) == expected
    assert sorted(cells["machine_id"].unique()) == expected


@needs_inputs
def test_input_provenance_hashes_the_actual_files():
    ids = ev.grid_machine_ids()
    prov = ev.input_provenance(ids)
    assert "data/container_grid/operator_signature_grid.json" in prov
    assert "data/container_grid/grid_manifest.json" in prov
    assert len(prov) == 2 + len(ids)
    sig = hashlib.sha256(ev.SIG.read_bytes()).hexdigest()
    assert prov["data/container_grid/operator_signature_grid.json"] == sig


# ------------------------------------------------------------- the metric helpers

def test_mape_mdape_known_values():
    y, p = [1.0, 2.0, 4.0], [2.0, 2.0, 2.0]
    assert ev.mape(y, p) == pytest.approx((100 + 0 + 50) / 3)
    assert ev.mdape(y, p) == pytest.approx(50.0)


def test_qerror_is_symmetric_and_at_least_one():
    a = ev.qerr([1.0, 4.0], [2.0, 2.0])
    b = ev.qerr([2.0, 2.0], [1.0, 4.0])
    assert a == b
    assert a[0] >= 1.0


def test_naive_predictor_is_scaling_one():
    import pandas as pd
    pairs = pd.DataFrame({"actual_scaling": [0.5, 2.0], "naive": [1.0, 1.0],
                          "prod": ["a", "b"], "query_id": ["q1", "q1"],
                          "operator": [0.5, 2.0]})
    assert ev.mape(pairs["actual_scaling"], pairs["naive"]) == pytest.approx(
        (abs(0.5 - 1) / 0.5 + abs(2 - 1) / 2) / 2 * 100)
    assert ev.mape(pairs["actual_scaling"], pairs["operator"]) == 0.0


def test_cluster_bootstrap_is_seeded_and_signed():
    import pandas as pd
    rows = []
    for m in "abcd":
        for q in range(5):
            rows.append({"prod": m, "query_id": f"q{q}", "actual_scaling": 2.0,
                         "good": 2.0, "bad": 1.0})
    pairs = pd.DataFrame(rows)
    r1 = ev.cluster_bootstrap(pairs, "bad", "good", n=200)
    r2 = ev.cluster_bootstrap(pairs, "bad", "good", n=200)
    assert r1 == r2                                   # deterministic
    assert r1["point_pp"] == pytest.approx(50.0)      # positive => second is better
    assert r1["excludes_zero"] is True


def test_few_shot_k0_equals_uncalibrated_mape():
    import pandas as pd
    rows = [{"prod": "a", "query_id": f"q{i}", "actual_scaling": 1.0 + i / 10,
             "operator": 1.0} for i in range(6)]
    pairs = pd.DataFrame(rows)
    k0 = [r for r in ev.few_shot_curve(pairs, ks=(0,)) if r["k"] == 0][0]
    assert k0["mape"] == pytest.approx(ev.mape(pairs["actual_scaling"], pairs["operator"]))


# ------------------------------------------------------- divergence from committed

@needs_inputs
def test_committed_metrics_were_generated_from_the_committed_inputs():
    """Fails when inputs change and the results are not regenerated, which is C10."""
    committed = json.loads(COMMITTED.read_text())
    assert "inputs_sha256" in committed, (
        "results/operator_model_metrics.json predates input provenance; "
        "regenerate with `make operator-results`")
    assert committed["inputs_sha256"] == ev.input_provenance(ev.grid_machine_ids()), (
        "an input of the per-operator evaluation changed after the committed results "
        "were generated; run `make operator-results` and review the diff")
    assert committed["grid"]["machines"] == sorted(ev.grid_machine_ids())


def _assert_close(a, b, path=""):
    if isinstance(a, dict):
        assert set(a) == set(b), f"{path}: keys differ"
        for k in a:
            _assert_close(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), f"{path}: length differs"
        for i, (x, y) in enumerate(zip(a, b)):
            _assert_close(x, y, f"{path}[{i}]")
    elif isinstance(a, float):
        assert a == pytest.approx(b, rel=1e-9, abs=1e-12), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


@needs_inputs
def test_regeneration_reproduces_the_committed_results(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ev, "RESULTS", tmp_path)
    ev.main()
    capsys.readouterr()
    regenerated = json.loads((tmp_path / "operator_model_metrics.json").read_text())
    committed = json.loads(COMMITTED.read_text())
    _assert_close(regenerated, committed)
    assert (tmp_path / "operator_model_pairs.csv").read_bytes() == COMMITTED_PAIRS.read_bytes()


@needs_inputs
def test_regeneration_is_deterministic(tmp_path, monkeypatch, capsys):
    outs = []
    for i in range(2):
        d = tmp_path / str(i)
        d.mkdir()
        monkeypatch.setattr(ev, "RESULTS", d)
        ev.main()
        outs.append((d / "operator_model_metrics.json").read_bytes())
    capsys.readouterr()
    assert outs[0] == outs[1]
