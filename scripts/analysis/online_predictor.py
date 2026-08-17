import json
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor

PROJECT_DIR = Path("/Users/harshithj/Main/Resources/CETP")
CSV_PATH = PROJECT_DIR / "data" / "raw" / "tpch_dataset.csv"
MODELS_DIR = PROJECT_DIR / "models"

RANDOM_STATE = 42
CONFIDENCE_THRESHOLD = 0.6
RETRAIN_TRIGGER_N = 10
INITIAL_SEEN_MACHINES = ["c7i", "m5a", "r5n"]

BOTTLENECK_LABELS = {
    1: "compute", 2: "mixed", 3: "mixed", 4: "io", 5: "mixed", 6: "bandwidth",
    7: "mixed", 8: "mixed", 9: "mixed", 10: "mixed", 11: "mixed", 12: "compute",
    13: "bandwidth", 14: "bandwidth", 16: "compute", 17: "io", 18: "mixed",
    19: "bandwidth", 20: "io", 21: "mixed", 22: "io",
}

HARDWARE_SIGNATURE = {
    "c5a": {"bandwidth": 14.9, "compute": 0.593},
    "z1d": {"bandwidth": 9.77, "compute": 0.882},
    "r5n": {"bandwidth": 9.39, "compute": 0.705},
    "m5a": {"bandwidth": 10.01, "compute": 0.457},
    "c7i": {"bandwidth": 8.44, "compute": 0.807},
}

ALLOWED_FEATURES = [
    "dev_cost", "dev_total_buffers", "dev_shared_hit", "dev_shared_read", "dev_rows", "dev_io_ratio",
    "bandwidth_ratio", "compute_ratio",
    "p_compute", "p_bandwidth", "p_io", "p_mixed",
    "analytical_scaling",
]

TARGETS = {0.5: "scaling_factor_p50", 0.95: "scaling_factor_p95", 0.99: "scaling_factor_p99"}

_bandwidths = [v["bandwidth"] for v in HARDWARE_SIGNATURE.values()]
_computes = [v["compute"] for v in HARDWARE_SIGNATURE.values()]
BANDWIDTH_MIN, BANDWIDTH_MAX = min(_bandwidths), max(_bandwidths)
COMPUTE_MIN, COMPUTE_MAX = min(_computes), max(_computes)


def normalized_signature(machine):
    sig = HARDWARE_SIGNATURE[machine]
    nb = (sig["bandwidth"] - BANDWIDTH_MIN) / (BANDWIDTH_MAX - BANDWIDTH_MIN)
    nc = (sig["compute"] - COMPUTE_MIN) / (COMPUTE_MAX - COMPUTE_MIN)
    return np.array([nb, nc])


def build_full_pairs_table(bottleneck_model, class_order):
    raw = pd.read_csv(CSV_PATH)
    raw["query_num"] = raw["query_id"].str.extract(r"q(\d+)").astype(int)

    agg = (
        raw.groupby(["machine_id", "query_id", "query_num"])
        .agg(
            time_p50=("time_ms", lambda x: np.percentile(x, 50)),
            time_p95=("time_ms", lambda x: np.percentile(x, 95)),
            time_p99=("time_ms", lambda x: np.percentile(x, 99)),
            cost=("cost", "first"),
            shared_hit=("shared_hit", "median"),
            shared_read=("shared_read", "median"),
            rows=("rows", "median"),
        )
        .reset_index()
    )
    agg["total_buffers"] = agg["shared_hit"] + agg["shared_read"]
    agg["io_ratio"] = agg["shared_read"] / (agg["total_buffers"] + 1)

    machines = sorted(agg["machine_id"].unique())
    pairs = []
    for dev in machines:
        for prod in machines:
            if dev == prod:
                continue
            dev_data = agg[agg["machine_id"] == dev].set_index("query_id")
            prod_data = agg[agg["machine_id"] == prod].set_index("query_id")
            common_queries = dev_data.index.intersection(prod_data.index)
            for q in common_queries:
                pairs.append({
                    "dev_machine": dev,
                    "prod_machine": prod,
                    "query_id": q,
                    "query_num": int(dev_data.loc[q, "query_num"]),
                    "dev_cost": dev_data.loc[q, "cost"],
                    "dev_shared_hit": dev_data.loc[q, "shared_hit"],
                    "dev_shared_read": dev_data.loc[q, "shared_read"],
                    "dev_total_buffers": dev_data.loc[q, "total_buffers"],
                    "dev_io_ratio": dev_data.loc[q, "io_ratio"],
                    "dev_rows": dev_data.loc[q, "rows"],
                    "dev_time_p50": dev_data.loc[q, "time_p50"],
                    "dev_time_p95": dev_data.loc[q, "time_p95"],
                    "dev_time_p99": dev_data.loc[q, "time_p99"],
                    "prod_time_p50": prod_data.loc[q, "time_p50"],
                    "prod_time_p95": prod_data.loc[q, "time_p95"],
                    "prod_time_p99": prod_data.loc[q, "time_p99"],
                })

    pairs_df = pd.DataFrame(pairs)
    pairs_df["bottleneck_class"] = pairs_df["query_num"].map(BOTTLENECK_LABELS)
    pairs_df["scaling_factor_p50"] = pairs_df["prod_time_p50"] / pairs_df["dev_time_p50"]
    pairs_df["scaling_factor_p95"] = pairs_df["prod_time_p95"] / pairs_df["dev_time_p95"]
    pairs_df["scaling_factor_p99"] = pairs_df["prod_time_p99"] / pairs_df["dev_time_p99"]

    pairs_df["bandwidth_ratio"] = pairs_df["prod_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["bandwidth"]) / \
        pairs_df["dev_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["bandwidth"])
    pairs_df["compute_ratio"] = pairs_df["prod_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["compute"]) / \
        pairs_df["dev_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["compute"])

    bottleneck_raw_cols = ["cost", "shared_hit", "shared_read", "total_buffers", "io_ratio", "rows"]
    bottleneck_X = pairs_df[["dev_cost", "dev_shared_hit", "dev_shared_read", "dev_total_buffers", "dev_io_ratio", "dev_rows"]]
    bottleneck_X.columns = bottleneck_raw_cols
    bottleneck_proba = bottleneck_model.predict_proba(bottleneck_X[bottleneck_raw_cols])
    for i, cls in enumerate(class_order):
        pairs_df[f"p_{cls}"] = bottleneck_proba[:, i]

    def analytical_scaling(row):
        inv_bandwidth = 1.0 / row["bandwidth_ratio"]
        inv_compute = 1.0 / row["compute_ratio"]
        mixed_term = np.sqrt(inv_bandwidth * inv_compute)
        w_bandwidth = row["p_bandwidth"] + row["p_io"]
        w_compute = row["p_compute"]
        w_mixed = row["p_mixed"]
        return w_bandwidth * inv_bandwidth + w_compute * inv_compute + w_mixed * mixed_term

    pairs_df["analytical_scaling"] = pairs_df.apply(analytical_scaling, axis=1)
    return pairs_df


def fit_scaling_models(training_pool):
    models = {}
    X = training_pool[ALLOWED_FEATURES].to_numpy()
    for alpha, target_col in TARGETS.items():
        model = GradientBoostingRegressor(
            loss="quantile", alpha=alpha, n_estimators=200, max_depth=3,
            learning_rate=0.05, random_state=RANDOM_STATE,
        )
        model.fit(X, training_pool[target_col].to_numpy())
        models[alpha] = model
    return models


def retrain_if_ready(training_pool, measured_buffer, n_trigger):
    if len(measured_buffer) < n_trigger:
        return None
    return fit_scaling_models(training_pool)


def confidence_gate(row, quantile_models, seen_machines, training_pool, threshold=CONFIDENCE_THRESHOLD):
    x = row[ALLOWED_FEATURES].to_numpy(dtype=float).reshape(1, -1)
    p50 = float(quantile_models[0.5].predict(x)[0])
    p95 = float(quantile_models[0.95].predict(x)[0])
    p99 = float(quantile_models[0.99].predict(x)[0])

    relative_width = (p99 - p50) / max(p50, 0.05)
    interval_component = 1.0 / (1.0 + max(relative_width, 0.0))

    prod_sig = normalized_signature(row["prod_machine"])
    seen_sigs = [normalized_signature(m) for m in seen_machines]
    min_dist = min(float(np.linalg.norm(prod_sig - s)) for s in seen_sigs)
    hw_component = max(0.0, 1.0 - min_dist / np.sqrt(2))

    if row["dev_machine"] in seen_machines:
        query_component = 1.0
    else:
        same_class_seen = (training_pool["bottleneck_class"] == row["bottleneck_class"]).any()
        query_component = 0.5 if same_class_seen else 0.0

    confidence = min(interval_component, hw_component, query_component)
    components = {
        "interval_component": interval_component,
        "hw_region_component": hw_component,
        "query_support_component": query_component,
        "predicted_p50": p50,
        "predicted_p95": p95,
        "predicted_p99": p99,
    }
    decision = "predict" if confidence >= threshold else "measure"
    return confidence, decision, components


def run_simulation(seed=RANDOM_STATE):
    bottleneck_model = joblib.load(MODELS_DIR / "bottleneck_classifier.pkl")
    class_order = list(bottleneck_model.classes_)
    pairs_df = build_full_pairs_table(bottleneck_model, class_order)

    initial_mask = pairs_df["dev_machine"].isin(INITIAL_SEEN_MACHINES) & pairs_df["prod_machine"].isin(INITIAL_SEEN_MACHINES)
    training_pool = pairs_df[initial_mask].reset_index(drop=True)
    stream_pool = pairs_df[~initial_mask].reset_index(drop=True)

    rng = np.random.default_rng(seed)
    stream_order = rng.permutation(len(stream_pool))

    seen_machines = set(INITIAL_SEEN_MACHINES)
    quantile_models = fit_scaling_models(training_pool)
    measured_buffer = []

    log = []
    for step, idx in enumerate(stream_order):
        row = stream_pool.iloc[idx]
        confidence, decision, components = confidence_gate(row, quantile_models, seen_machines, training_pool)

        blind_pred_p50 = components["predicted_p50"]
        actual_p50 = float(row["scaling_factor_p50"])
        blind_error_pct = abs(blind_pred_p50 - actual_p50) / actual_p50 * 100

        entry = {
            "step": step,
            "dev_machine": row["dev_machine"],
            "prod_machine": row["prod_machine"],
            "query_id": row["query_id"],
            "confidence": confidence,
            "decision": decision,
            "predicted_p50": blind_pred_p50,
            "actual_p50": actual_p50,
            "blind_error_pct": blind_error_pct,
            "interval_component": components["interval_component"],
            "hw_region_component": components["hw_region_component"],
            "query_support_component": components["query_support_component"],
        }

        if decision == "predict":
            entry["gated_error_pct"] = blind_error_pct
        else:
            entry["gated_error_pct"] = None
            new_row = row.to_frame().T
            training_pool = pd.concat([training_pool, new_row], ignore_index=True)
            measured_buffer.append(row)
            seen_machines.add(row["dev_machine"])
            seen_machines.add(row["prod_machine"])
            retrained = retrain_if_ready(training_pool, measured_buffer, RETRAIN_TRIGGER_N)
            if retrained is not None:
                quantile_models = retrained
                entry["retrained_after_this_step"] = True
                measured_buffer = []
            else:
                entry["retrained_after_this_step"] = False

        log.append(entry)

    return pd.DataFrame(log), training_pool


def summarize(log_df):
    n_total = len(log_df)
    n_predict = (log_df["decision"] == "predict").sum()
    n_measure = (log_df["decision"] == "measure").sum()

    predict_subset = log_df[log_df["decision"] == "predict"]
    predicted_subset_mape = predict_subset["gated_error_pct"].mean()
    blind_all_mape = log_df["blind_error_pct"].mean()

    print("=" * 78)
    print("CONFIDENCE GATE DEFINITION")
    print("=" * 78)
    print(f"confidence = min(interval_component, hw_region_component, query_support_component)  [weakest-link: any one low signal caps confidence]")
    print(f"  interval_component = 1 / (1 + (p99-p50)/max(p50, 0.05))          [wide interval -> lower score]")
    print(f"  hw_region_component = 1 - min_dist(prod_sig, seen_machine_sigs)/sqrt(2)  [normalized bandwidth/compute space]")
    print(f"  query_support_component = 1.0 if dev_machine seen, else 0.5 if same bottleneck class seen else 0.0")
    print(f"threshold = {CONFIDENCE_THRESHOLD}  (>= threshold -> PREDICT, else -> MEASURE)")
    print(f"retrain trigger N = {RETRAIN_TRIGGER_N} newly measured rows")
    streamed_machines = sorted(set(HARDWARE_SIGNATURE.keys()) - set(INITIAL_SEEN_MACHINES))
    print(f"initial seen machines = {INITIAL_SEEN_MACHINES}, streamed-in machines = {streamed_machines}")

    print("\n" + "=" * 78)
    print("SIMULATION RESULT")
    print("=" * 78)
    print(f"Total streamed requests: {n_total}")
    print(f"Predicted: {n_predict} ({100*n_predict/n_total:.1f}%)   Measured: {n_measure} ({100*n_measure/n_total:.1f}%)")
    print(f"\nPredicted-subset MAPE (only rows the gate chose to predict): {predicted_subset_mape:.2f}%")
    print(f"Blind-all MAPE (model prediction vs actual on every streamed row, gate ignored): {blind_all_mape:.2f}%")
    verdict = "gate meaningfully improves predicted-subset accuracy" if predicted_subset_mape < blind_all_mape - 1.0 else \
        "gate does NOT meaningfully improve predicted-subset accuracy over blind prediction"
    print(f"VERDICT: {verdict} (predicted-subset MAPE {predicted_subset_mape:.2f}% vs blind-all MAPE {blind_all_mape:.2f}%)")

    print("\n" + "=" * 78)
    print("MEASUREMENT-COST CURVE (deciles of the stream)")
    print("=" * 78)
    n_bins = 10
    log_df = log_df.copy()
    log_df["bin"] = pd.cut(log_df["step"], bins=n_bins, labels=False)
    print(f"{'decile':<10}{'n':>6}{'measured %':>14}{'cum. predicted MAPE %':>26}")
    cum_errors = []
    for b in range(n_bins):
        chunk = log_df[log_df["bin"] == b]
        measured_pct = (chunk["decision"] == "measure").mean() * 100
        cum_predict = log_df[(log_df["bin"] <= b) & (log_df["decision"] == "predict")]
        cum_mape = cum_predict["gated_error_pct"].mean() if len(cum_predict) else float("nan")
        cum_errors.append(cum_mape)
        print(f"{b:<10}{len(chunk):>6}{measured_pct:>13.1f}%{cum_mape:>25.2f}%")

    first_half_measure_rate = (log_df[log_df["step"] < n_total // 2]["decision"] == "measure").mean() * 100
    second_half_measure_rate = (log_df[log_df["step"] >= n_total // 2]["decision"] == "measure").mean() * 100
    print(f"\nMeasured rate, first half of stream:  {first_half_measure_rate:.1f}%")
    print(f"Measured rate, second half of stream: {second_half_measure_rate:.1f}%")
    trend = "decreased" if second_half_measure_rate < first_half_measure_rate else "did not decrease"
    print(f"Measurement need {trend} over the course of the stream.")

    return {
        "n_total": n_total, "n_predict": int(n_predict), "n_measure": int(n_measure),
        "predicted_subset_mape": predicted_subset_mape, "blind_all_mape": blind_all_mape,
        "first_half_measure_rate": first_half_measure_rate, "second_half_measure_rate": second_half_measure_rate,
    }


if __name__ == "__main__":
    log_df, final_training_pool = run_simulation()
    stats = summarize(log_df)
    RESULTS_DIR = PROJECT_DIR / "results"
    RESULTS_DIR.mkdir(exist_ok=True)
    log_df.drop(columns=["retrained_after_this_step"], errors="ignore").to_csv(RESULTS_DIR / "online_predictor_simulation_log.csv", index=False)
    with open(RESULTS_DIR / "online_predictor_summary.json", "w") as f:
        json.dump(stats, f, indent=2)
    print(f"\nSaved simulation log to {RESULTS_DIR / 'online_predictor_simulation_log.csv'}")
    print(f"Saved summary to {RESULTS_DIR / 'online_predictor_summary.json'}")
