import json
import numpy as np
import pandas as pd
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor

PROJECT_DIR = Path("/Users/harshithj/Main/Resources/CETP")
CSV_PATH = PROJECT_DIR / "data" / "raw" / "tpch_dataset.csv"
MODELS_DIR = PROJECT_DIR / "models"
RESULTS_DIR = PROJECT_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

RANDOM_STATE = 42
N_RANDOM_TRIALS = 500
OPERATING_THRESHOLD = 0.6

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

_bandwidths = [v["bandwidth"] for v in HARDWARE_SIGNATURE.values()]
_computes = [v["compute"] for v in HARDWARE_SIGNATURE.values()]
BANDWIDTH_MIN, BANDWIDTH_MAX = min(_bandwidths), max(_bandwidths)
COMPUTE_MIN, COMPUTE_MAX = min(_computes), max(_computes)


def normalized_signature(machine):
    sig = HARDWARE_SIGNATURE[machine]
    nb = (sig["bandwidth"] - BANDWIDTH_MIN) / (BANDWIDTH_MAX - BANDWIDTH_MIN)
    nc = (sig["compute"] - COMPUTE_MIN) / (COMPUTE_MAX - COMPUTE_MIN)
    return np.array([nb, nc])


def hw_region_confidence(prod_machine, training_machines):
    prod_sig = normalized_signature(prod_machine)
    train_sigs = [normalized_signature(m) for m in training_machines]
    min_dist = min(float(np.linalg.norm(prod_sig - s)) for s in train_sigs)
    return max(0.0, 1.0 - min_dist / np.sqrt(2))


def build_pairs_table():
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
    return pairs_df, machines


def fit_p50_model(train_df):
    model = GradientBoostingRegressor(
        loss="quantile", alpha=0.5, n_estimators=200, max_depth=3,
        learning_rate=0.05, random_state=RANDOM_STATE,
    )
    model.fit(train_df[ALLOWED_FEATURES].to_numpy(), train_df["scaling_factor_p50"].to_numpy())
    return model


def build_lomo_predictions(pairs_df, machines):
    fold_frames = []
    for held_out in machines:
        seen = [m for m in machines if m != held_out]
        train_mask = pairs_df["dev_machine"].isin(seen) & pairs_df["prod_machine"].isin(seen)
        test_mask = (pairs_df["dev_machine"] == held_out) | (pairs_df["prod_machine"] == held_out)
        train_df = pairs_df[train_mask]
        test_df = pairs_df[test_mask].copy()

        model = fit_p50_model(train_df)
        test_df["predicted_p50"] = model.predict(test_df[ALLOWED_FEATURES].to_numpy())
        test_df["held_out_machine"] = held_out
        test_df["hw_confidence"] = test_df["prod_machine"].apply(lambda m: hw_region_confidence(m, seen))
        fold_frames.append(test_df)

    result = pd.concat(fold_frames, ignore_index=True)
    result["abs_pct_error"] = (result["predicted_p50"] - result["scaling_factor_p50"]).abs() / result["scaling_factor_p50"] * 100
    return result


def mape_of(df):
    return float(df["abs_pct_error"].mean())


def risk_coverage_curve(results_df, coverage_levels):
    sorted_df = results_df.sort_values("hw_confidence", ascending=False).reset_index(drop=True)
    n = len(sorted_df)
    rows = []
    for c in coverage_levels:
        k = max(1, int(round(c * n)))
        subset = sorted_df.iloc[:k]
        rows.append({"coverage": c, "n": k, "selective_risk": mape_of(subset)})
    return pd.DataFrame(rows)


def random_abstention_curve(results_df, coverage_levels, n_trials, seed):
    rng = np.random.default_rng(seed)
    n = len(results_df)
    errors = results_df["abs_pct_error"].to_numpy()
    rows = []
    for c in coverage_levels:
        k = max(1, int(round(c * n)))
        trial_risks = np.empty(n_trials)
        for t in range(n_trials):
            idx = rng.choice(n, size=k, replace=False)
            trial_risks[t] = errors[idx].mean()
        rows.append({
            "coverage": c, "n": k,
            "selective_risk_mean": float(trial_risks.mean()),
            "selective_risk_min": float(trial_risks.min()),
            "selective_risk_max": float(trial_risks.max()),
        })
    return pd.DataFrame(rows)


def area_under_curve(coverage, risk):
    order = np.argsort(coverage)
    return float(np.trapz(np.array(risk)[order], np.array(coverage)[order]))


def plot_risk_coverage(hw_curve, random_curve, save_path):
    fig, ax = plt.subplots(figsize=(6.5, 5))
    ax.plot(hw_curve["coverage"], hw_curve["selective_risk"], marker="o", color="#1f77b4", label="hardware-distance abstention")
    ax.plot(random_curve["coverage"], random_curve["selective_risk_mean"], marker="s", color="#7f7f7f", label="random abstention (mean of 500 trials)")
    ax.fill_between(random_curve["coverage"], random_curve["selective_risk_min"], random_curve["selective_risk_max"], color="#7f7f7f", alpha=0.15)
    ax.set_xlabel("coverage (fraction of requests predicted, not abstained)")
    ax.set_ylabel("selective risk: MAPE on predicted subset (%)")
    ax.set_title("Risk-coverage curve: hardware-distance vs random abstention")
    ax.invert_xaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    pairs_df, machines = build_pairs_table()
    results_df = build_lomo_predictions(pairs_df, machines)
    results_df.to_csv(RESULTS_DIR / "confidence_abstention_lomo_predictions.csv", index=False)

    print("=" * 78)
    print("CONFIDENCE SIGNAL DEFINITION")
    print("=" * 78)
    print("confidence = hw_region_confidence(prod_machine, training_machines)")
    print("  = 1 - min_dist(normalized_signature(prod_machine), {normalized_signature(m): m in training_machines}) / sqrt(2)")
    print("  normalization: bandwidth and compute each min-max scaled across the known 5-machine universe")
    print("  distance: Euclidean in 2D normalized (bandwidth, compute) space")
    print("  training_machines = the 4 machines NOT held out in that row's leave-one-machine-out fold")
    print(f"Evaluated under leave-one-machine-out CV: {len(results_df)} (row, fold) evaluations across {len(machines)} folds")
    print(f"Overall blind MAPE (no abstention): {mape_of(results_df):.2f}%")

    coverage_levels = np.round(np.arange(1.00, 0.04, -0.05), 2)
    hw_curve = risk_coverage_curve(results_df, coverage_levels)
    random_curve = random_abstention_curve(results_df, coverage_levels, N_RANDOM_TRIALS, RANDOM_STATE)

    print("\n" + "=" * 78)
    print("RISK-COVERAGE CURVE")
    print("=" * 78)
    print(f"{'coverage':>10}{'n':>6}{'hw-distance selective risk %':>32}{'random selective risk % (mean)':>34}")
    for c in coverage_levels:
        hw_row = hw_curve[hw_curve["coverage"] == c].iloc[0]
        rand_row = random_curve[random_curve["coverage"] == c].iloc[0]
        print(f"{c:>10.2f}{hw_row['n']:>6}{hw_row['selective_risk']:>32.2f}{rand_row['selective_risk_mean']:>34.2f}")

    aurc_hw = area_under_curve(hw_curve["coverage"], hw_curve["selective_risk"])
    aurc_random = area_under_curve(random_curve["coverage"], random_curve["selective_risk_mean"])
    print(f"\nArea under risk-coverage curve (lower is better):")
    print(f"  hardware-distance abstention: {aurc_hw:.3f}")
    print(f"  random abstention:            {aurc_random:.3f}")
    verdict = "beats" if aurc_hw < aurc_random else "does NOT beat"
    print(f"  VERDICT: hardware-distance abstention {verdict} random abstention on area under the curve.")

    print("\n" + "=" * 78)
    print(f"OPERATING POINT (threshold = {OPERATING_THRESHOLD})")
    print("=" * 78)
    predicted = results_df[results_df["hw_confidence"] >= OPERATING_THRESHOLD]
    abstained = results_df[results_df["hw_confidence"] < OPERATING_THRESHOLD]
    coverage_pct = 100 * len(predicted) / len(results_df)
    abstain_pct = 100 * len(abstained) / len(results_df)
    selective_mape = mape_of(predicted) if len(predicted) else float("nan")
    abstained_mape_if_predicted = mape_of(abstained) if len(abstained) else float("nan")
    print(f"At confidence >= {OPERATING_THRESHOLD}: predicts {len(predicted)}/{len(results_df)} requests ({coverage_pct:.1f}% coverage) at {selective_mape:.2f}% MAPE")
    print(f"Defers the remaining {len(abstained)} requests ({abstain_pct:.1f}%) to measurement")
    print(f"(had those deferred requests been predicted anyway, their MAPE would have been {abstained_mape_if_predicted:.2f}%, "
          f"i.e. what abstention avoided)")

    plot_risk_coverage(hw_curve, random_curve, RESULTS_DIR / "risk_coverage_curve.png")

    hw_curve.to_csv(RESULTS_DIR / "risk_coverage_hw_distance.csv", index=False)
    random_curve.to_csv(RESULTS_DIR / "risk_coverage_random.csv", index=False)

    summary = {
        "overall_blind_mape": mape_of(results_df),
        "aurc_hw_distance": aurc_hw,
        "aurc_random": aurc_random,
        "operating_threshold": OPERATING_THRESHOLD,
        "operating_coverage_pct": coverage_pct,
        "operating_selective_mape": selective_mape,
        "operating_abstain_pct": abstain_pct,
    }
    with open(RESULTS_DIR / "confidence_abstention_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nSaved: {RESULTS_DIR / 'risk_coverage_curve.png'}")
    print(f"Saved: {RESULTS_DIR / 'risk_coverage_hw_distance.csv'}")
    print(f"Saved: {RESULTS_DIR / 'risk_coverage_random.csv'}")
    print(f"Saved: {RESULTS_DIR / 'confidence_abstention_summary.json'}")
