import inspect
import json
import random
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
GATE_DIR = PROJECT_DIR / "gate"
sys.path.insert(0, str(GATE_DIR))

import cetp_gate
from cetp_gate import (
    RAW_BOTTLENECK_COLS,
    features_from_explain_json,
    derive_dev_features,
    load_config,
    load_models,
    hardware_distance_confidence,
    resolve_signature,
    resolve_sla_ms,
    run_gate,
)
from cetp_gate_demo import SCENARIOS

random.seed(42)
np.random.seed(42)

FIXTURES_DIR = Path(__file__).resolve().parent / "test_fixtures"
GATE_SCRIPT = GATE_DIR / "cetp_gate.py"
CONFIG_PATH = GATE_DIR / "cetp.yml"
EXAMPLES_DIR = GATE_DIR / "examples" / "gate_queries"

REAL_EXPLAIN_Q19 = FIXTURES_DIR / "q19_local_real_explain.json"
REAL_EXPLAIN_Q1 = FIXTURES_DIR / "q1_local_real_explain.json"
REAL_EXPLAIN_Q18 = FIXTURES_DIR / "q18_local_real_explain.json"

FORBIDDEN_SUBSTRINGS = ["prod_time", "prod_buffer", "prod_shared", "prod_rows", "prod_cost", "prod_hit", "prod_read"]

KNOWN_ANSWER_TRIPLES = [
    {
        "label": "c5a->c7i q19",
        "dev_features_raw": features_from_explain_json(FIXTURES_DIR / "q19_local_real_explain.json"),
        "dev_machine": "c5a", "prod_machine": "c7i",
        "actual_scaling_factor": None,
        "expected_p50": 1.5082, "expected_p95": 1.8258, "expected_p99": 2.9630,
    },
    {
        "label": "r5n->z1d q1",
        "dev_features_raw": features_from_explain_json(FIXTURES_DIR / "q1_local_real_explain.json"),
        "dev_machine": "r5n", "prod_machine": "z1d",
        "actual_scaling_factor": None,
        "expected_p50": 0.8756, "expected_p95": 1.4837, "expected_p99": 3.0071,
    },
    {
        "label": "c7i->r5n q18",
        "dev_features_raw": features_from_explain_json(FIXTURES_DIR / "q18_local_real_explain.json"),
        "dev_machine": "c7i", "prod_machine": "r5n",
        "actual_scaling_factor": None,
        "expected_p50": 1.1380, "expected_p95": 1.4818, "expected_p99": 2.3697,
    },
    # q12 and q6 have no raw EXPLAIN plan retained from the original dataset collection run
    # (scripts/collection/collect_query.py only ever wrote the pre-reduced, buffer-inflated dict to disk,
    # never the underlying "Plan" tree). These two dev_features_raw dicts are therefore still the
    # inflated values collect_query.py's old sum_buffers() wrote into tpch_dataset.csv, kept as a
    # frozen regression lock on the model's arithmetic only -- not verified against a real plan.
    {
        "label": "m5a->z1d q12",
        "dev_features_raw": {"cost": 210511.91, "shared_hit": 1747.0, "shared_read": 137123.0, "rows": 2.0, "time_ms": 1108.8780000000002},
        "dev_machine": "m5a", "prod_machine": "z1d",
        "actual_scaling_factor": 1.1201,
        "expected_p50": 1.1216, "expected_p95": 1.1220, "expected_p99": 6.0030,
    },
    {
        "label": "z1d->c5a q6",
        "dev_features_raw": {"cost": 168633.05, "shared_hit": 0.0, "shared_read": 105469.0, "rows": 1.0, "time_ms": 887.3354999999999},
        "dev_machine": "z1d", "prod_machine": "c5a",
        "actual_scaling_factor": 0.5705,
        "expected_p50": 0.5707, "expected_p95": 1.2974, "expected_p99": 2.1417,
    },
]

TOLERANCE = 1e-3


@pytest.fixture(scope="module")
def hw_table():
    _, _, table, _ = load_models()
    return table


def run_cli(args):
    return subprocess.run(
        [sys.executable, str(GATE_SCRIPT)] + args,
        capture_output=True, text=True, cwd=str(PROJECT_DIR),
    )


class TestFullChainHappyPath:
    def test_cli_explain_in_verdict_out(self):
        proc = run_cli([
            "--explain-json", str(REAL_EXPLAIN_Q19),
            "--query-id", "q19",
            "--config", str(CONFIG_PATH),
            "--json",
        ])
        assert proc.returncode in (0, 1), f"unexpected exit code {proc.returncode}, stderr={proc.stderr}"
        payload = json.loads(proc.stdout)
        state = payload["verdict"]["state"]
        assert state in ("PASS", "BLOCK", "WARN")
        for k in ("p50", "p95", "p99"):
            v = payload["verdict"]["predicted_prod_ms"][k]
            assert isinstance(v, (int, float)) and np.isfinite(v)

    def test_direct_call_matches_explain_parsing_path(self):
        raw_features = features_from_explain_json(REAL_EXPLAIN_Q19)
        result = run_gate(raw_features, {"bandwidth": 8.44, "compute": 0.807}, {"bandwidth": 14.9, "compute": 0.593}, sla_ms=200.0)
        assert result["verdict"]["state"] in ("PASS", "BLOCK", "WARN")
        for k in ("p50", "p95", "p99"):
            assert np.isfinite(result["predicted_scaling_factor"][k])


class TestKnownAnswerRegression:
    @pytest.mark.parametrize("triple", KNOWN_ANSWER_TRIPLES, ids=[t["label"] for t in KNOWN_ANSWER_TRIPLES])
    def test_prediction_matches_locked_value(self, triple, hw_table):
        dev_sig = hw_table[triple["dev_machine"]]
        prod_sig = hw_table[triple["prod_machine"]]
        result = run_gate(triple["dev_features_raw"], dev_sig, prod_sig, sla_ms=1e9)
        pred = result["predicted_scaling_factor"]
        actual_scaling = triple["actual_scaling_factor"]
        actual_scaling_str = f"{actual_scaling:.4f}" if actual_scaling is not None else "n/a (real-fixture-derived, no matched prod measurement retained)"
        print(
            f"\n[{triple['label']}] actual_scaling={actual_scaling_str} "
            f"predicted_p50={pred['p50']:.4f} (expected {triple['expected_p50']:.4f}) "
            f"predicted_p95={pred['p95']:.4f} (expected {triple['expected_p95']:.4f}) "
            f"predicted_p99={pred['p99']:.4f} (expected {triple['expected_p99']:.4f})"
        )
        assert abs(pred["p50"] - triple["expected_p50"]) < TOLERANCE
        assert abs(pred["p95"] - triple["expected_p95"]) < TOLERANCE
        assert abs(pred["p99"] - triple["expected_p99"]) < TOLERANCE


class TestAllFourVerdictBranchesFire:
    def test_all_states_and_low_confidence_reason_present(self):
        seen_states = set()
        low_confidence_seen = False
        for scenario in SCENARIOS:
            dev_features_raw = json.loads(scenario["dev_features_path"].read_text())
            result = run_gate(
                dev_features_raw,
                scenario["dev_signature"],
                scenario["prod_signature"],
                scenario["sla_ms"],
            )
            state = result["verdict"]["state"]
            seen_states.add(state)
            if "LOW CONFIDENCE" in result["verdict"]["reason"]:
                low_confidence_seen = True
        assert "PASS" in seen_states
        assert "BLOCK" in seen_states
        assert "WARN" in seen_states
        assert low_confidence_seen

    def test_block_exit_code_is_one(self):
        block_scenario = next(s for s in SCENARIOS if s["label"] == "BLOCK")
        dev_features_raw = json.loads(block_scenario["dev_features_path"].read_text())
        tmp_features = FIXTURES_DIR / "_tmp_block_features.json"
        tmp_features.write_text(json.dumps(dev_features_raw))
        try:
            proc = run_cli([
                "--dev-features", str(tmp_features),
                "--query-id", "q1",
                "--dev-bandwidth", str(block_scenario["dev_signature"]["bandwidth"]),
                "--dev-compute", str(block_scenario["dev_signature"]["compute"]),
                "--prod-bandwidth", str(block_scenario["prod_signature"]["bandwidth"]),
                "--prod-compute", str(block_scenario["prod_signature"]["compute"]),
                "--sla-ms", str(block_scenario["sla_ms"]),
            ])
        finally:
            tmp_features.unlink()
        assert proc.returncode == 1, f"stdout={proc.stdout} stderr={proc.stderr}"
        assert "BLOCK" in proc.stdout

    def test_pass_and_warn_exit_code_is_zero(self):
        for label in ("PASS", "WARN (in-between)"):
            scenario = next(s for s in SCENARIOS if s["label"] == label)
            dev_features_raw = json.loads(scenario["dev_features_path"].read_text())
            tmp_features = FIXTURES_DIR / "_tmp_nonblock_features.json"
            tmp_features.write_text(json.dumps(dev_features_raw))
            try:
                proc = run_cli([
                    "--dev-features", str(tmp_features),
                    "--query-id", "q",
                    "--dev-bandwidth", str(scenario["dev_signature"]["bandwidth"]),
                    "--dev-compute", str(scenario["dev_signature"]["compute"]),
                    "--prod-bandwidth", str(scenario["prod_signature"]["bandwidth"]),
                    "--prod-compute", str(scenario["prod_signature"]["compute"]),
                    "--sla-ms", str(scenario["sla_ms"]),
                ])
            finally:
                tmp_features.unlink()
            assert proc.returncode == 0, f"[{label}] stdout={proc.stdout} stderr={proc.stderr}"


class TestLeakageGuard:
    def test_scaling_feature_columns_has_no_prod_side_columns(self):
        _, _, _, feature_columns = load_models()
        for col in feature_columns:
            lowered = col.lower()
            for bad in FORBIDDEN_SUBSTRINGS:
                assert bad not in lowered, f"forbidden prod-side column '{col}' found in scaling_feature_columns.json"

    def test_bottleneck_raw_cols_has_no_prod_side_columns(self):
        for col in RAW_BOTTLENECK_COLS:
            lowered = col.lower()
            for bad in FORBIDDEN_SUBSTRINGS:
                assert bad not in lowered, f"forbidden prod-side column '{col}' found in RAW_BOTTLENECK_COLS"

    def test_predict_scaling_row_values_keys_match_feature_columns_and_are_clean(self):
        _, _, _, feature_columns = load_models()
        source = inspect.getsource(cetp_gate.predict_scaling)
        dict_body = re.search(r"row_values\s*=\s*\{(.*?)\}\n", source, re.DOTALL).group(1)
        keys = re.findall(r'"([a-zA-Z0-9_]+)":', dict_body)
        assert set(keys) == set(feature_columns), (
            f"predict_scaling's row_values keys {sorted(keys)} do not match "
            f"scaling_feature_columns.json {sorted(feature_columns)}"
        )
        for key in keys:
            lowered = key.lower()
            for bad in FORBIDDEN_SUBSTRINGS:
                assert bad not in lowered, f"forbidden prod-side key '{key}' built into predict_scaling's row_values"


class TestMalformedInputHandling:
    def test_truncated_explain_blob_raises_cleanly(self, tmp_path):
        full_text = REAL_EXPLAIN_Q19.read_text()
        truncated = tmp_path / "truncated.json"
        truncated.write_text(full_text[: len(full_text) // 2])
        with pytest.raises(json.JSONDecodeError) as exc_info:
            features_from_explain_json(truncated)
        print(f"\ntruncated EXPLAIN blob -> {type(exc_info.value).__name__}: {exc_info.value}")

    def test_missing_hardware_signature_raises_cleanly(self):
        dev_features_raw = {"cost": 1.0, "shared_hit": 1.0, "shared_read": 1.0, "rows": 1.0, "time_ms": 1.0}
        incomplete_dev_signature = {"bandwidth": 14.9}
        with pytest.raises(KeyError) as exc_info:
            run_gate(dev_features_raw, incomplete_dev_signature, {"bandwidth": 8.44, "compute": 0.807}, sla_ms=500)
        print(f"\nmissing hardware signature -> {type(exc_info.value).__name__}: {exc_info.value}")

    def test_unexpected_plan_structure_missing_plan_key_raises_cleanly(self, tmp_path):
        bad = tmp_path / "no_plan.json"
        bad.write_text(json.dumps([{"Execution Time": 10.0}]))
        with pytest.raises(KeyError) as exc_info:
            features_from_explain_json(bad)
        print(f"\nEXPLAIN JSON missing 'Plan' key -> {type(exc_info.value).__name__}: {exc_info.value}")

    def test_unexpected_plan_structure_empty_result_raises_cleanly(self, tmp_path):
        bad = tmp_path / "empty.json"
        bad.write_text(json.dumps([]))
        with pytest.raises(IndexError) as exc_info:
            features_from_explain_json(bad)
        print(f"\nempty EXPLAIN result array -> {type(exc_info.value).__name__}: {exc_info.value}")

    def test_plan_missing_actual_rows_raises_cleanly_at_derive_step(self, tmp_path):
        bad = tmp_path / "no_rows.json"
        bad.write_text(json.dumps([{"Plan": {"Node Type": "Result", "Total Cost": 1.0}, "Execution Time": 5.0}]))
        raw = features_from_explain_json(bad)
        assert raw["rows"] is None
        with pytest.raises(TypeError) as exc_info:
            derive_dev_features(raw)
        print(f"\nplan node missing 'Actual Rows' -> {type(exc_info.value).__name__}: {exc_info.value}")

    def test_negative_sla_is_rejected(self):
        dev_features_raw = json.loads((EXAMPLES_DIR / "q19_c5a_dev_features.json").read_text())
        with pytest.raises(ValueError):
            run_gate(dev_features_raw, {"bandwidth": 14.9, "compute": 0.593}, {"bandwidth": 8.44, "compute": 0.807}, sla_ms=-50.0)

    def test_zero_sla_is_rejected(self):
        dev_features_raw = json.loads((EXAMPLES_DIR / "q19_c5a_dev_features.json").read_text())
        with pytest.raises(ValueError):
            run_gate(dev_features_raw, {"bandwidth": 14.9, "compute": 0.593}, {"bandwidth": 8.44, "compute": 0.807}, sla_ms=0.0)


class TestConfigRoundTrip:
    def test_config_signatures_and_default_sla_propagate(self):
        config = load_config(CONFIG_PATH)
        dev_signature = resolve_signature(config, "dev_hardware", None, None)
        prod_signature = resolve_signature(config, "prod_hardware", None, None)
        assert dev_signature == {"bandwidth": config["dev_hardware"]["bandwidth_gbs"], "compute": config["dev_hardware"]["compute_score"]}
        assert prod_signature == {"bandwidth": config["prod_hardware"]["bandwidth_gbs"], "compute": config["prod_hardware"]["compute_score"]}
        assert resolve_sla_ms(config, "q1", None) == config["sla"]["per_query"]["q1"]
        assert resolve_sla_ms(config, "unknown_query", None) == config["sla"]["default_ms"]

    def test_changing_sla_in_config_changes_verdict(self, tmp_path):
        config = load_config(CONFIG_PATH)
        dev_features_raw = json.loads((EXAMPLES_DIR / "q19_c5a_dev_features.json").read_text())
        dev_signature = resolve_signature(config, "dev_hardware", None, None)
        prod_signature = resolve_signature(config, "prod_hardware", None, None)

        generous_config = json.loads(json.dumps(config))
        generous_config["sla"]["per_query"]["q19"] = 100000.0
        strict_config = json.loads(json.dumps(config))
        strict_config["sla"]["per_query"]["q19"] = 0.001

        generous_sla = resolve_sla_ms(generous_config, "q19", None)
        strict_sla = resolve_sla_ms(strict_config, "q19", None)

        generous_result = run_gate(dev_features_raw, dev_signature, prod_signature, generous_sla)
        strict_result = run_gate(dev_features_raw, dev_signature, prod_signature, strict_sla)

        assert generous_result["verdict"]["state"] == "PASS"
        assert strict_result["verdict"]["state"] == "BLOCK"

    def test_cli_config_flag_reaches_prediction(self, tmp_path):
        strict_config = yaml.safe_load(CONFIG_PATH.read_text())
        strict_config["sla"]["per_query"]["q19"] = 0.001
        strict_config_path = tmp_path / "strict_cetp.yml"
        strict_config_path.write_text(yaml.safe_dump(strict_config))

        proc = run_cli([
            "--explain-json", str(REAL_EXPLAIN_Q19),
            "--query-id", "q19",
            "--config", str(strict_config_path),
        ])
        assert proc.returncode == 1
        assert "BLOCK" in proc.stdout


class TestConfidenceBoundary:
    def test_far_out_signature_flags_low_confidence(self, hw_table):
        far_signature = {"bandwidth": 200.0, "compute": 20.0}
        confidence = hardware_distance_confidence(far_signature, hw_table)
        assert confidence < 0.6

    def test_in_distribution_signature_is_high_confidence(self, hw_table):
        confidence = hardware_distance_confidence(hw_table["c5a"], hw_table)
        assert confidence >= 0.6

    def test_low_confidence_routes_to_warn_and_defer(self, hw_table):
        dev_features_raw = json.loads((EXAMPLES_DIR / "q19_c7i_dev_features.json").read_text())
        result = run_gate(dev_features_raw, hw_table["c7i"], {"bandwidth": 30.0, "compute": 1.4}, sla_ms=100.0)
        assert result["verdict"]["state"] == "WARN"
        assert "LOW CONFIDENCE" in result["verdict"]["reason"]
        assert result["verdict"]["low_confidence"] is True


class TestBufferAccounting:
    def test_shared_buffer_totals_match_postgres_cumulative_semantics(self):
        raw = json.loads(REAL_EXPLAIN_Q1.read_text())
        root = raw[0]["Plan"]
        true_shared_hit = root["Shared Hit Blocks"]
        true_shared_read = root["Shared Read Blocks"]

        parsed = features_from_explain_json(REAL_EXPLAIN_Q1)

        assert parsed["shared_hit"] == true_shared_hit, (
            f"buffer extraction double-counts: got {parsed['shared_hit']}, "
            f"but Postgres already reports the cumulative total at the root node as {true_shared_hit}"
        )
        assert parsed["shared_read"] == true_shared_read, (
            f"buffer extraction double-counts: got {parsed['shared_read']}, "
            f"but Postgres already reports the cumulative total at the root node as {true_shared_read}"
        )

    def test_collection_script_and_gate_parser_share_identical_buffer_logic(self):
        collect_script = (PROJECT_DIR / "scripts" / "collection" / "collect_query.py").read_text()
        gate_body = inspect.getsource(cetp_gate.root_buffers).split("\n", 1)[1]
        collect_func = collect_script.split("def root_buffers(plan_node, key):", 1)[1]
        collect_body = collect_func.split("\n\n", 1)[0]

        def normalize(s):
            return re.sub(r"\s+", " ", s).strip()

        assert normalize(gate_body) == normalize(collect_body), (
            "cetp_gate.root_buffers() and scripts/collection/collect_query.py's root_buffers() have diverged: "
            "the CI-time EXPLAIN parser and the offline dataset-collection parser must stay identical "
            "or predictions will be trained on a different feature definition than they are served with."
        )
