import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor

PROJECT_DIR = Path("/Users/harshithj/Main/Resources/CETP")
CSV_PATH = PROJECT_DIR / "tpch_dataset.csv"
MODELS_DIR = PROJECT_DIR / "models"
RESULTS_DIR = PROJECT_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

RANDOM_STATE = 42
COMPUTE_GATE_THRESHOLD = 0.5

BOTTLENECK_LABELS = {
    1: "compute", 2: "mixed", 3: "mixed", 4: "io", 5: "mixed", 6: "bandwidth",
    7: "mixed", 8: "mixed", 9: "mixed", 10: "mixed", 11: "mixed", 12: "compute",
    13: "bandwidth", 14: "bandwidth", 16: "compute", 17: "io", 18: "mixed",
    19: "bandwidth", 20: "io", 21: "mixed", 22: "io",
}

BANDWIDTH_ONLY = {"c5a": 14.9, "z1d": 9.77, "r5n": 9.39, "m5a": 10.01, "c7i": 8.44}
OLD_COMPUTE = {"c5a": 0.593, "z1d": 0.882, "r5n": 0.705, "m5a": 0.457, "c7i": 0.807}
NEW_COMPUTE_SINGLE = {"c5a": 10.2585, "z1d": 15.8112, "r5n": 12.6870, "m5a": 8.0354, "c7i": 12.9979}
NEW_COMPUTE_MULTI = {"c5a": 40.8071, "z1d": 28.0804, "r5n": 24.9681, "m5a": 31.5366, "c7i": 25.8909}

ALLOWED_FEATURES = [
    "dev_cost", "dev_total_buffers", "dev_shared_hit", "dev_shared_read", "dev_rows", "dev_io_ratio",
    "bandwidth_ratio", "compute_ratio",
    "p_compute", "p_bandwidth", "p_io", "p_mixed",
    "analytical_scaling",
]


def build_pairs(compute_scores):
    bottleneck_model = joblib.load(MODELS_DIR / "bottleneck_classifier.pkl")
    class_order = list(bottleneck_model.classes_)

    raw = pd.read_csv(CSV_PATH)
    raw["query_num"] = raw["query_id"].str.extract(r"q(\d+)").astype(int)

    agg = (
        raw.groupby(["machine_id", "query_id", "query_num"])
        .agg(
            time_p50=("time_ms", lambda x: np.percentile(x, 50)),
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
                    "prod_time_p50": prod_data.loc[q, "time_p50"],
                })

    pairs_df = pd.DataFrame(pairs)
    pairs_df["bottleneck_class"] = pairs_df["query_num"].map(BOTTLENECK_LABELS)
    pairs_df["scaling_factor_p50"] = pairs_df["prod_time_p50"] / pairs_df["dev_time_p50"]
    pairs_df["bandwidth_ratio"] = pairs_df["prod_machine"].map(lambda m: BANDWIDTH_ONLY[m]) / \
        pairs_df["dev_machine"].map(lambda m: BANDWIDTH_ONLY[m])
    pairs_df["compute_ratio"] = pairs_df["prod_machine"].map(lambda m: compute_scores[m]) / \
        pairs_df["dev_machine"].map(lambda m: compute_scores[m])

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

    def gated_scaling(row):
        if row["p_compute"] >= COMPUTE_GATE_THRESHOLD:
            return 1.0 / row["compute_ratio"]
        return 1.0 / row["bandwidth_ratio"]

    pairs_df["analytical_scaling"] = pairs_df.apply(analytical_scaling, axis=1)
    pairs_df["gated_scaling"] = pairs_df.apply(gated_scaling, axis=1)
    return pairs_df, machines


def fit_p50_model(train_df):
    model = GradientBoostingRegressor(
        loss="quantile", alpha=0.5, n_estimators=200, max_depth=3,
        learning_rate=0.05, random_state=RANDOM_STATE,
    )
    model.fit(train_df[ALLOWED_FEATURES].to_numpy(), train_df["scaling_factor_p50"].to_numpy())
    return model


def mape(y_true, y_pred):
    return float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100)


def lomo_mape_table(pairs_df, machines):
    fold_frames = []
    for held_out in machines:
        seen = [m for m in machines if m != held_out]
        train_mask = pairs_df["dev_machine"].isin(seen) & pairs_df["prod_machine"].isin(seen)
        test_mask = (pairs_df["dev_machine"] == held_out) | (pairs_df["prod_machine"] == held_out)
        train_df = pairs_df[train_mask]
        test_df = pairs_df[test_mask].copy()
        model = fit_p50_model(train_df)
        test_df["learned_pred"] = model.predict(test_df[ALLOWED_FEATURES].to_numpy())
        fold_frames.append(test_df)
    return pd.concat(fold_frames, ignore_index=True)


def report(label, compute_scores, old_corr=None):
    print("=" * 78)
    print(f"COMPUTE SIGNAL: {label}")
    print("=" * 78)
    pairs_df, machines = build_pairs(compute_scores)

    pooled_corr = pairs_df["compute_ratio"].corr(pairs_df["scaling_factor_p50"])
    compute_q = pairs_df[pairs_df["bottleneck_class"] == "compute"]
    compute_corr = compute_q["compute_ratio"].corr(compute_q["scaling_factor_p50"])
    print(f"compute_ratio vs scaling_factor_p50, pooled (n={len(pairs_df)}): {pooled_corr:.3f}"
          + (f"  (was {old_corr:.3f})" if old_corr is not None else ""))
    print(f"compute_ratio vs scaling_factor_p50, compute-labeled queries (n={len(compute_q)}): {compute_corr:.3f}")

    naive_linear_mape = mape(pairs_df["scaling_factor_p50"].to_numpy(), (1.0 / pairs_df["bandwidth_ratio"]).to_numpy())
    gated_mape = mape(pairs_df["scaling_factor_p50"].to_numpy(), pairs_df["gated_scaling"].to_numpy())
    analytical_mape = mape(pairs_df["scaling_factor_p50"].to_numpy(), pairs_df["analytical_scaling"].to_numpy())
    print(f"\n(single-pass, all 420 pairs, no CV) naive-linear MAPE: {naive_linear_mape:.2f}%  "
          f"gated-formula MAPE: {gated_mape:.2f}%  analytical MAPE: {analytical_mape:.2f}%")
    gate_verdict = "beats" if gated_mape < naive_linear_mape else "does NOT beat"
    print(f"Gated formula {gate_verdict} flat naive-linear.")

    lomo = lomo_mape_table(pairs_df, machines)
    naive_lomo = mape(lomo["scaling_factor_p50"].to_numpy(), np.ones(len(lomo)))
    naive_linear_lomo = mape(lomo["scaling_factor_p50"].to_numpy(), (1.0 / lomo["bandwidth_ratio"]).to_numpy())
    analytical_lomo = mape(lomo["scaling_factor_p50"].to_numpy(), lomo["analytical_scaling"].to_numpy())
    gated_lomo = mape(lomo["scaling_factor_p50"].to_numpy(), lomo["gated_scaling"].to_numpy())
    learned_lomo = mape(lomo["scaling_factor_p50"].to_numpy(), lomo["learned_pred"].to_numpy())

    print("\nLeave-one-machine-out MAPE comparison:")
    print(f"{'approach':<38}{'MAPE %':>10}")
    print(f"{'naive (=1.0)':<38}{naive_lomo:>10.2f}")
    print(f"{'naive-linear (1/bandwidth_ratio)':<38}{naive_linear_lomo:>10.2f}")
    print(f"{'analytical roofline':<38}{analytical_lomo:>10.2f}")
    print(f"{'bottleneck-gated':<38}{gated_lomo:>10.2f}")
    print(f"{'learned quantile (p50)':<38}{learned_lomo:>10.2f}")
    beats = [name for name, val in [("analytical", analytical_lomo), ("gated", gated_lomo), ("learned", learned_lomo)] if val < naive_linear_lomo]
    print(f"\nApproaches beating naive-linear ({naive_linear_lomo:.2f}%) under LOMO: {beats if beats else 'NONE'}")
    print()
    return {
        "label": label, "pooled_corr": pooled_corr, "compute_query_corr": compute_corr,
        "naive_linear_lomo": naive_linear_lomo, "analytical_lomo": analytical_lomo,
        "gated_lomo": gated_lomo, "learned_lomo": learned_lomo,
    }


if __name__ == "__main__":
    old_result = report("OLD compute benchmark (scalar accumulation loop)", OLD_COMPUTE)
    new_single_result = report("NEW compute benchmark, single-threaded GFLOPS", NEW_COMPUTE_SINGLE, old_corr=old_result["pooled_corr"])
    new_multi_result = report("NEW compute benchmark, multi-threaded aggregate GFLOPS", NEW_COMPUTE_MULTI, old_corr=old_result["pooled_corr"])

    summary = pd.DataFrame([old_result, new_single_result, new_multi_result])
    summary.to_csv(RESULTS_DIR / "addition4_reevaluation.csv", index=False)
    print(f"Saved: {RESULTS_DIR / 'addition4_reevaluation.csv'}")
