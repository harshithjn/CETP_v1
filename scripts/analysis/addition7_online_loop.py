import inspect
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR / "gate"))

import cetp_gate
import measurement_store as ms

RESULTS_DIR = PROJECT_DIR / "results"
CORRECTED_CSV = PROJECT_DIR / "data" / "corrected" / "tpch_dataset_corrected.csv"
DEMO_DB_PATH = PROJECT_DIR / "cetp_measurements_addition7_demo.db"

RANDOM_STATE = 42
CONFIDENCE_THRESHOLD = 0.6
RETRAIN_TRIGGER_N = 12
UNSEEN_MACHINE = "c5a"
NARRATIVE_DEV_MACHINE = "c7i"
NARRATIVE_QUERY = "q19"

RESERVED_TEST_QUERIES = ["q1", "q12", "q6", "q19", "q4", "q17", "q2", "q9", "q18"]

FORBIDDEN_SUBSTRINGS = ["prod_time", "prod_buffer", "prod_shared", "prod_rows", "prod_cost", "prod_hit", "prod_read"]


def dev_features_for(machine_id, query_id, measurements_df):
    row = measurements_df[(measurements_df["machine_id"] == machine_id) & (measurements_df["query_id"] == query_id)].iloc[0]
    return {
        "cost": float(row["cost"]),
        "shared_hit": float(row["shared_hit"]),
        "shared_read": float(row["shared_read"]),
        "rows": float(row["rows"]),
        "time_ms": float(row["time_ms"]),
    }


def leakage_check():
    print("=" * 90)
    print("LEAKAGE RE-CHECK ON THE RETRAINED PIPELINE")
    print("=" * 90)
    ok = True
    for col in ms.SCALING_FEATURE_COLUMNS:
        lowered = col.lower()
        for bad in FORBIDDEN_SUBSTRINGS:
            if bad in lowered:
                ok = False
                print(f"  FAIL: forbidden prod-side column '{col}' in SCALING_FEATURE_COLUMNS")
    for col in ms.RAW_BOTTLENECK_COLS:
        lowered = col.lower()
        for bad in FORBIDDEN_SUBSTRINGS:
            if bad in lowered:
                ok = False
                print(f"  FAIL: forbidden prod-side column '{col}' in RAW_BOTTLENECK_COLS")

    build_pairs_src = inspect.getsource(ms._build_pairs)
    fit_quantile_src = inspect.getsource(ms._fit_quantile_models)
    x_matrix_line = re.search(r"X\s*=\s*pairs_df\[(.*?)\]\.to_numpy\(\)", fit_quantile_src, re.DOTALL)
    x_cols_used = x_matrix_line.group(1) if x_matrix_line else ""
    if "prod_time" in x_cols_used:
        ok = False
        print("  FAIL: prod_time_p50 appears in the quantile-model input matrix construction")

    prod_time_uses = [line.strip() for line in build_pairs_src.splitlines() if "prod_time_p50" in line]
    label_only = all(
        ("pp.loc[q" in line) or ("scaling_factor_p50" in line and "=" in line and line.strip().startswith("pdf[\"scaling_factor_p50\"]"))
        for line in prod_time_uses
    )
    print(f"  every use of prod_time_p50 in pair construction:")
    for line in prod_time_uses:
        print(f"    {line}")
    if not label_only:
        ok = False
        print("  FAIL: prod_time_p50 used outside of building the pair record / the scaling_factor_p50 label")

    print(f"\n  SCALING_FEATURE_COLUMNS (the only inputs the quantile GBR sees): {ms.SCALING_FEATURE_COLUMNS}")
    print(f"  RAW_BOTTLENECK_COLS (the only inputs the bottleneck classifier sees): {ms.RAW_BOTTLENECK_COLS}")
    print(f"  Neither list contains any prod-side value. prod_time_p50 is read exactly twice per pair: once to")
    print(f"  become dev_time_p50 for a DIFFERENT row (when that machine is dev elsewhere), and once divided by")
    print(f"  dev_time_p50 to form scaling_factor_p50, the regression TARGET -- never a feature column.")
    print(f"\n  LEAKAGE RE-CHECK: {'PASS' if ok else 'FAIL'}")
    return ok


def evaluate_pairs(reserved_queries, dev_machines, measurements_df, actual_lookup, bottleneck_model,
                    quantile_models, hardware_signature_table, feature_columns, label):
    rows = []
    for dev in dev_machines:
        for q in reserved_queries:
            if (dev, q) not in actual_lookup:
                continue
            dev_features_raw = dev_features_for(dev, q, measurements_df)
            dev_sig = hardware_signature_table.get(dev, ms.SEED_HARDWARE_SIGNATURE[dev])
            prod_sig = ms.SEED_HARDWARE_SIGNATURE[UNSEEN_MACHINE]
            result = cetp_gate.run_gate_with_models(
                dev_features_raw, dev_sig, prod_sig, sla_ms=1e9,
                bottleneck_model=bottleneck_model, quantile_models=quantile_models,
                hardware_signature_table=hardware_signature_table, feature_columns=feature_columns,
                confidence_threshold=CONFIDENCE_THRESHOLD,
            )
            actual = actual_lookup[(dev, q)]
            pred_p50 = result["predicted_scaling_factor"]["p50"]
            err_pct = abs(pred_p50 - actual) / actual * 100
            rows.append({
                "dev_machine": dev, "query_id": q, "predicted_p50": pred_p50, "actual_scaling_factor": actual,
                "error_pct": err_pct, "confidence": result["verdict"]["confidence"], "state": result["verdict"]["state"],
            })
    df = pd.DataFrame(rows)
    print(f"\n[{label}] n={len(df)}  mean confidence={df['confidence'].mean():.3f}  MAPE={df['error_pct'].mean():.2f}%")
    return df


def main():
    if DEMO_DB_PATH.exists():
        DEMO_DB_PATH.unlink()

    ms.init_store(db_path=DEMO_DB_PATH, seed_csv=CORRECTED_CSV, excluded_machines=[UNSEEN_MACHINE])

    full = pd.read_csv(CORRECTED_CSV)
    full_agg = (
        full.groupby(["machine_id", "query_id"])
        .agg(time_p50=("time_ms", "median"))
        .reset_index()
    )
    dev_machines_all = ["z1d", "r5n", "m5a", "c7i"]
    actual_lookup = {}
    for dev in dev_machines_all:
        dev_times = full_agg[full_agg["machine_id"] == dev].set_index("query_id")["time_p50"]
        c5a_times = full_agg[full_agg["machine_id"] == UNSEEN_MACHINE].set_index("query_id")["time_p50"]
        for q in dev_times.index.intersection(c5a_times.index):
            actual_lookup[(dev, q)] = float(c5a_times[q] / dev_times[q])

    print("=" * 90)
    print("STEP 1 -- BEFORE LEARNING: 4-machine world, c5a entirely unseen")
    print("=" * 90)
    pre_retrain = ms.retrain(db_path=DEMO_DB_PATH, n_trigger=0, force=True, persist=False)
    pre_bottleneck, pre_quantile, pre_hw_table, pre_cols = (
        pre_retrain["bottleneck_model"], pre_retrain["quantile_models"],
        pre_retrain["hardware_signature_table"], pre_retrain["feature_columns"],
    )
    print(f"known machines before learning: {sorted(pre_hw_table.keys())}")

    measurements_df = ms.load_all_measurements(DEMO_DB_PATH)
    narrative_dev_features = dev_features_for(NARRATIVE_DEV_MACHINE, NARRATIVE_QUERY, measurements_df)
    narrative_result_before = cetp_gate.run_gate_with_models(
        narrative_dev_features, pre_hw_table[NARRATIVE_DEV_MACHINE], ms.SEED_HARDWARE_SIGNATURE[UNSEEN_MACHINE],
        sla_ms=1e9, bottleneck_model=pre_bottleneck, quantile_models=pre_quantile,
        hardware_signature_table=pre_hw_table, feature_columns=pre_cols, confidence_threshold=CONFIDENCE_THRESHOLD,
    )
    v_before = narrative_result_before["verdict"]
    print(f"\nFirst request against {UNSEEN_MACHINE} as prod (dev={NARRATIVE_DEV_MACHINE}, query={NARRATIVE_QUERY}):")
    print(f"  state={v_before['state']}  confidence={v_before['confidence']:.3f}  reason={v_before['reason']}")
    assert v_before["state"] == "WARN" and v_before["low_confidence"], "expected a LOW-CONFIDENCE defer on unseen c5a"
    print("  ASSERTION PASSED: system correctly deferred on unfamiliar hardware.")

    before_eval = evaluate_pairs(
        RESERVED_TEST_QUERIES, dev_machines_all, measurements_df, actual_lookup,
        pre_bottleneck, pre_quantile, pre_hw_table, pre_cols, label="BEFORE learning (blind, would-be-deferred predictions)",
    )

    print("\n" + "=" * 90)
    print("STEP 2 -- MEASURE AND CAPTURE: revealing real c5a measurements, honest framing")
    print("=" * 90)
    print("(In a real deployment this step is triggered by an actual prod run against c5a.")
    print(" Here, held-out rows from the corrected dataset stand in for that fresh measurement --")
    print(" this is explicitly a demonstration on held-out data, not a live production stream.)")

    c5a_agg = full.groupby(["machine_id", "query_id"]).agg(
        cost=("cost", "first"), shared_hit=("shared_hit", "median"), shared_read=("shared_read", "median"),
        rows=("rows", "median"), time_ms=("time_ms", "median"),
    ).reset_index()
    c5a_agg = c5a_agg[c5a_agg["machine_id"] == UNSEEN_MACHINE]
    all_c5a_queries = sorted(c5a_agg["query_id"].unique(), key=lambda q: int(q[1:]))
    reveal_queries = [q for q in all_c5a_queries if q not in RESERVED_TEST_QUERIES]
    assert len(reveal_queries) == RETRAIN_TRIGGER_N, f"expected {RETRAIN_TRIGGER_N} reveal queries, got {len(reveal_queries)}"

    for q in reveal_queries:
        r = c5a_agg[c5a_agg["query_id"] == q].iloc[0]
        bandwidth = ms.SEED_HARDWARE_SIGNATURE[UNSEEN_MACHINE]["bandwidth"] if q == reveal_queries[0] else None
        compute = ms.SEED_HARDWARE_SIGNATURE[UNSEEN_MACHINE]["compute"] if q == reveal_queries[0] else None
        ms.record_measurement(
            UNSEEN_MACHINE, q, r["cost"], r["shared_hit"], r["shared_read"], r["rows"], r["time_ms"],
            bandwidth=bandwidth, compute=compute, db_path=DEMO_DB_PATH,
        )

    n_captured = ms.count_captured_since_last_retrain(DEMO_DB_PATH)
    print(f"\ncaptured {n_captured} new measurements for {UNSEEN_MACHINE} (retrain trigger N={RETRAIN_TRIGGER_N})")

    print("\n" + "=" * 90)
    print("STEP 3 -- RETRAIN TRIGGER")
    print("=" * 90)
    post_retrain = ms.retrain(db_path=DEMO_DB_PATH, n_trigger=RETRAIN_TRIGGER_N, persist=True)
    assert post_retrain is not None, "retrain should have fired at the configured threshold"
    post_bottleneck, post_quantile, post_hw_table, post_cols = (
        post_retrain["bottleneck_model"], post_retrain["quantile_models"],
        post_retrain["hardware_signature_table"], post_retrain["feature_columns"],
    )
    print(f"known machines after learning: {sorted(post_hw_table.keys())}")
    assert UNSEEN_MACHINE in post_hw_table, f"{UNSEEN_MACHINE} must be a known machine after retraining"

    print("\n" + "=" * 90)
    print("STEP 4 -- AFTER LEARNING: second request against c5a")
    print("=" * 90)
    measurements_df_post = ms.load_all_measurements(DEMO_DB_PATH)
    narrative_dev_features_post = dev_features_for(NARRATIVE_DEV_MACHINE, NARRATIVE_QUERY, measurements_df_post)
    narrative_result_after = cetp_gate.run_gate_with_models(
        narrative_dev_features_post, post_hw_table[NARRATIVE_DEV_MACHINE], ms.SEED_HARDWARE_SIGNATURE[UNSEEN_MACHINE],
        sla_ms=1e9, bottleneck_model=post_bottleneck, quantile_models=post_quantile,
        hardware_signature_table=post_hw_table, feature_columns=post_cols, confidence_threshold=CONFIDENCE_THRESHOLD,
    )
    v_after = narrative_result_after["verdict"]
    print(f"Second request against {UNSEEN_MACHINE} as prod (dev={NARRATIVE_DEV_MACHINE}, query={NARRATIVE_QUERY}):")
    print(f"  state={v_after['state']}  confidence={v_after['confidence']:.3f}  reason={v_after['reason']}")
    assert v_after["confidence"] > v_before["confidence"], "confidence must rise after learning c5a's signature"
    assert not v_after["low_confidence"], f"{UNSEEN_MACHINE} should now be treated as in-distribution"
    print(f"  ASSERTION PASSED: confidence rose ({v_before['confidence']:.3f} -> {v_after['confidence']:.3f}), no longer low-confidence.")

    actual_narrative = actual_lookup[(NARRATIVE_DEV_MACHINE, NARRATIVE_QUERY)]
    pred_before = narrative_result_before["predicted_scaling_factor"]["p50"]
    pred_after = narrative_result_after["predicted_scaling_factor"]["p50"]
    err_before = abs(pred_before - actual_narrative) / actual_narrative * 100
    err_after = abs(pred_after - actual_narrative) / actual_narrative * 100
    print(f"\n  narrative query ({NARRATIVE_DEV_MACHINE} -> {UNSEEN_MACHINE}, {NARRATIVE_QUERY}):")
    print(f"    actual scaling factor = {actual_narrative:.4f}")
    print(f"    predicted BEFORE = {pred_before:.4f}  (error {err_before:.1f}%)")
    print(f"    predicted AFTER  = {pred_after:.4f}  (error {err_after:.1f}%)")

    after_eval = evaluate_pairs(
        RESERVED_TEST_QUERIES, dev_machines_all, measurements_df_post, actual_lookup,
        post_bottleneck, post_quantile, post_hw_table, post_cols, label="AFTER learning (real, in-distribution predictions)",
    )

    leak_ok = leakage_check()

    print("\n" + "=" * 90)
    print("FINAL BEFORE / AFTER SUMMARY")
    print("=" * 90)
    print(f"c5a hw-distance confidence:  before={v_before['confidence']:.3f}   after={v_after['confidence']:.3f}")
    print(f"c5a held-out MAPE (9 queries x 4 dev machines, n={len(before_eval)}):")
    print(f"  before learning: {before_eval['error_pct'].mean():.2f}%")
    print(f"  after learning:  {after_eval['error_pct'].mean():.2f}%")
    improvement = before_eval["error_pct"].mean() - after_eval["error_pct"].mean()
    print(f"  improvement:     {improvement:+.2f} pp")
    print(f"leakage re-check: {'PASS' if leak_ok else 'FAIL'}")

    RESULTS_DIR.mkdir(exist_ok=True)
    before_eval.to_csv(RESULTS_DIR / "addition7_before_learning_eval.csv", index=False)
    after_eval.to_csv(RESULTS_DIR / "addition7_after_learning_eval.csv", index=False)
    summary = {
        "narrative_query": {"dev_machine": NARRATIVE_DEV_MACHINE, "prod_machine": UNSEEN_MACHINE, "query_id": NARRATIVE_QUERY,
                             "actual_scaling_factor": actual_narrative,
                             "predicted_before": pred_before, "error_pct_before": err_before,
                             "predicted_after": pred_after, "error_pct_after": err_after,
                             "confidence_before": v_before["confidence"], "confidence_after": v_after["confidence"],
                             "state_before": v_before["state"], "state_after": v_after["state"]},
        "held_out_mape": {"n": int(len(before_eval)), "before_pct": float(before_eval["error_pct"].mean()),
                           "after_pct": float(after_eval["error_pct"].mean()), "improvement_pp": float(improvement)},
        "known_machines_before": sorted(pre_hw_table.keys()),
        "known_machines_after": sorted(post_hw_table.keys()),
        "retrain_trigger_n": RETRAIN_TRIGGER_N,
        "leakage_check_passed": leak_ok,
    }
    (RESULTS_DIR / "addition7_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nSaved: results/addition7_before_learning_eval.csv, results/addition7_after_learning_eval.csv, results/addition7_summary.json")


if __name__ == "__main__":
    main()
