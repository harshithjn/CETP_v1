import json
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import precision_score, recall_score

PROJECT_DIR = Path("/Users/harshithj/Main/Resources/CETP")
CSV_PATH = PROJECT_DIR / "data" / "raw" / "tpch_dataset.csv"
MODELS_DIR = PROJECT_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

RANDOM_STATE = 42
SLA_SCALING_THRESHOLD = 1.5

HARDWARE_SIGNATURE = {
    "c5a": {"bandwidth": 14.9, "compute": 0.593},
    "z1d": {"bandwidth": 9.77, "compute": 0.882},
    "r5n": {"bandwidth": 9.39, "compute": 0.705},
    "m5a": {"bandwidth": 10.01, "compute": 0.457},
    "c7i": {"bandwidth": 8.44, "compute": 0.807},
}

bottleneck_model = joblib.load(MODELS_DIR / "bottleneck_classifier.pkl")
BOTTLENECK_CLASS_ORDER = list(bottleneck_model.classes_)

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
                "query_num": dev_data.loc[q, "query_num"],
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
pairs_df["scaling_factor_p50"] = pairs_df["prod_time_p50"] / pairs_df["dev_time_p50"]
pairs_df["scaling_factor_p95"] = pairs_df["prod_time_p95"] / pairs_df["dev_time_p95"]
pairs_df["scaling_factor_p99"] = pairs_df["prod_time_p99"] / pairs_df["dev_time_p99"]

pairs_df["dev_bandwidth"] = pairs_df["dev_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["bandwidth"])
pairs_df["prod_bandwidth"] = pairs_df["prod_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["bandwidth"])
pairs_df["dev_compute"] = pairs_df["dev_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["compute"])
pairs_df["prod_compute"] = pairs_df["prod_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["compute"])
pairs_df["bandwidth_ratio"] = pairs_df["prod_bandwidth"] / pairs_df["dev_bandwidth"]
pairs_df["compute_ratio"] = pairs_df["prod_compute"] / pairs_df["dev_compute"]

bottleneck_feature_cols = ["dev_cost", "dev_shared_hit", "dev_shared_read", "dev_total_buffers", "dev_io_ratio", "dev_rows"]
bottleneck_raw_cols = ["cost", "shared_hit", "shared_read", "total_buffers", "io_ratio", "rows"]
bottleneck_X = pairs_df[bottleneck_feature_cols].rename(columns=dict(zip(bottleneck_feature_cols, bottleneck_raw_cols)))
bottleneck_X = bottleneck_X[bottleneck_raw_cols]
bottleneck_proba = bottleneck_model.predict_proba(bottleneck_X)
for i, cls in enumerate(BOTTLENECK_CLASS_ORDER):
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

ALLOWED_FEATURES = [
    "dev_cost", "dev_total_buffers", "dev_shared_hit", "dev_shared_read", "dev_rows", "dev_io_ratio",
    "bandwidth_ratio", "compute_ratio",
    "p_compute", "p_bandwidth", "p_io", "p_mixed",
    "analytical_scaling",
]

TARGETS = {
    0.5: "scaling_factor_p50",
    0.95: "scaling_factor_p95",
    0.99: "scaling_factor_p99",
}

fold_predictions = []
for held_out in machines:
    test_mask = (pairs_df["dev_machine"] == held_out) | (pairs_df["prod_machine"] == held_out)
    train_mask = ~test_mask
    train_df = pairs_df[train_mask]
    test_df = pairs_df[test_mask]

    X_train = train_df[ALLOWED_FEATURES].to_numpy()
    X_test = test_df[ALLOWED_FEATURES].to_numpy()

    quantile_preds = {}
    for alpha, target_col in TARGETS.items():
        model = GradientBoostingRegressor(
            loss="quantile",
            alpha=alpha,
            n_estimators=200,
            max_depth=3,
            learning_rate=0.05,
            random_state=RANDOM_STATE,
        )
        model.fit(X_train, train_df[target_col].to_numpy())
        quantile_preds[alpha] = model.predict(X_test)

    fold_out = test_df[[
        "dev_machine", "prod_machine", "query_id",
        "scaling_factor_p50", "scaling_factor_p95", "scaling_factor_p99",
        "bandwidth_ratio", "analytical_scaling",
    ]].copy()
    fold_out["held_out_machine"] = held_out
    fold_out["naive_pred"] = 1.0
    fold_out["naive_linear_pred"] = 1.0 / fold_out["bandwidth_ratio"]
    fold_out["analytical_pred"] = fold_out["analytical_scaling"]
    fold_out["learned_p50"] = quantile_preds[0.5]
    fold_out["learned_p95"] = quantile_preds[0.95]
    fold_out["learned_p99"] = quantile_preds[0.99]
    fold_predictions.append(fold_out)

cv_results = pd.concat(fold_predictions, ignore_index=True)

def mape(y_true, y_pred):
    return float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100)

approaches = {
    "naive (=1.0)": "naive_pred",
    "naive-linear (1/bandwidth_ratio)": "naive_linear_pred",
    "analytical roofline": "analytical_pred",
    "learned quantile (p50 model)": "learned_p50",
}

print("=" * 78)
print("LEAVE-ONE-MACHINE-OUT CV: MAPE ON scaling_factor_p50 (headline)")
print("=" * 78)
print(f"{'approach':<38}{'MAPE %':>12}")
for name, col in approaches.items():
    m = mape(cv_results["scaling_factor_p50"].to_numpy(), cv_results[col].to_numpy())
    print(f"{name:<38}{m:>12.2f}")

print(f"\n(evaluated over {len(cv_results)} (row, held-out-machine) predictions;")
print(f" each of the {len(pairs_df)} pairs is evaluated twice, once per participating machine's held-out fold)")

print("\n" + "=" * 78)
print("QUANTILE CALIBRATION (nominal alpha vs empirical coverage, single-sided)")
print("=" * 78)
print("Each target (scaling_factor_p50/p95/p99) is its own noisy label, not nested")
print("percentiles of one distribution, so calibration is checked per-target: does")
print("actual <= predicted hold at the rate implied by that quantile's alpha.\n")
print(f"{'alpha':<10}{'target':<24}{'empirical coverage %':>22}{'n':>8}")
for alpha, target_col in TARGETS.items():
    pred_col = {"scaling_factor_p50": "learned_p50", "scaling_factor_p95": "learned_p95", "scaling_factor_p99": "learned_p99"}[target_col]
    coverage = float(np.mean(cv_results[target_col].to_numpy() <= cv_results[pred_col].to_numpy()) * 100)
    print(f"{alpha:<10}{target_col:<24}{coverage:>22.1f}{len(cv_results):>8}")

print("\n" + "=" * 78)
print(f"SLA-DECISION ACCURACY (breach = scaling_factor_p99 > {SLA_SCALING_THRESHOLD})")
print("=" * 78)
true_breach = (cv_results["scaling_factor_p99"] > SLA_SCALING_THRESHOLD).astype(int)
pred_breach = (cv_results["learned_p99"] > SLA_SCALING_THRESHOLD).astype(int)
pred_breach_analytical = (cv_results["analytical_pred"] > SLA_SCALING_THRESHOLD).astype(int)

prec_learned = precision_score(true_breach, pred_breach, zero_division=0)
rec_learned = recall_score(true_breach, pred_breach, zero_division=0)
prec_analytical = precision_score(true_breach, pred_breach_analytical, zero_division=0)
rec_analytical = recall_score(true_breach, pred_breach_analytical, zero_division=0)

print(f"True breach rate: {true_breach.mean()*100:.1f}% ({true_breach.sum()}/{len(true_breach)})")
print(f"Learned p99 model   -> precision={prec_learned:.3f}  recall={rec_learned:.3f}")
print(f"Analytical roofline -> precision={prec_analytical:.3f}  recall={rec_analytical:.3f}")

print("\n" + "=" * 78)
print("PER-MACHINE BREAKDOWN (MAPE on scaling_factor_p50, learned model)")
print("=" * 78)
print(f"{'held-out machine':<20}{'n rows':>10}{'learned MAPE %':>18}{'analytical MAPE %':>20}")
per_machine_rows = []
for m in machines:
    sub = cv_results[cv_results["held_out_machine"] == m]
    learned_m = mape(sub["scaling_factor_p50"].to_numpy(), sub["learned_p50"].to_numpy())
    analytical_m = mape(sub["scaling_factor_p50"].to_numpy(), sub["analytical_pred"].to_numpy())
    print(f"{m:<20}{len(sub):>10}{learned_m:>18.2f}{analytical_m:>20.2f}")
    per_machine_rows.append({"machine": m, "n": len(sub), "learned_mape": learned_m, "analytical_mape": analytical_m})

final_models = {}
X_all = pairs_df[ALLOWED_FEATURES].to_numpy()
for alpha, target_col in TARGETS.items():
    model = GradientBoostingRegressor(
        loss="quantile",
        alpha=alpha,
        n_estimators=200,
        max_depth=3,
        learning_rate=0.05,
        random_state=RANDOM_STATE,
    )
    model.fit(X_all, pairs_df[target_col].to_numpy())
    final_models[alpha] = model

joblib.dump(final_models, MODELS_DIR / "scaling_quantile_models.pkl")
joblib.dump(HARDWARE_SIGNATURE, MODELS_DIR / "hardware_signature.pkl")
with open(MODELS_DIR / "scaling_feature_columns.json", "w") as f:
    json.dump(ALLOWED_FEATURES, f, indent=2)

print(f"\nSaved quantile models to {MODELS_DIR / 'scaling_quantile_models.pkl'}")
print(f"Saved hardware signature table to {MODELS_DIR / 'hardware_signature.pkl'}")


def predict_scaling(dev_features, bandwidth_ratio, compute_ratio, bottleneck_probs):
    models = joblib.load(MODELS_DIR / "scaling_quantile_models.pkl")
    inv_bandwidth = 1.0 / bandwidth_ratio
    inv_compute = 1.0 / compute_ratio
    mixed_term = np.sqrt(inv_bandwidth * inv_compute)
    p_compute = bottleneck_probs.get("compute", 0.0)
    p_bandwidth = bottleneck_probs.get("bandwidth", 0.0)
    p_io = bottleneck_probs.get("io", 0.0)
    p_mixed = bottleneck_probs.get("mixed", 0.0)
    analytical = (p_bandwidth + p_io) * inv_bandwidth + p_compute * inv_compute + p_mixed * mixed_term

    row = [
        dev_features["cost"], dev_features["total_buffers"], dev_features["shared_hit"],
        dev_features["shared_read"], dev_features["rows"], dev_features["io_ratio"],
        bandwidth_ratio, compute_ratio, p_compute, p_bandwidth, p_io, p_mixed, analytical,
    ]
    x = np.array([row])
    return {
        "p50": float(models[0.5].predict(x)[0]),
        "p95": float(models[0.95].predict(x)[0]),
        "p99": float(models[0.99].predict(x)[0]),
    }


if __name__ == "__main__":
    example = pairs_df.iloc[0]
    result = predict_scaling(
        dev_features={
            "cost": example["dev_cost"],
            "total_buffers": example["dev_total_buffers"],
            "shared_hit": example["dev_shared_hit"],
            "shared_read": example["dev_shared_read"],
            "rows": example["dev_rows"],
            "io_ratio": example["dev_io_ratio"],
        },
        bandwidth_ratio=example["bandwidth_ratio"],
        compute_ratio=example["compute_ratio"],
        bottleneck_probs={
            "compute": example["p_compute"],
            "bandwidth": example["p_bandwidth"],
            "io": example["p_io"],
            "mixed": example["p_mixed"],
        },
    )
    print(f"\nExample predict_scaling() call for {example['dev_machine']} -> {example['prod_machine']}, {example['query_id']}:")
    print(f"  actual scaling_factor_p50={example['scaling_factor_p50']:.3f}, p95={example['scaling_factor_p95']:.3f}, p99={example['scaling_factor_p99']:.3f}")
    print(f"  predicted: {result}")
