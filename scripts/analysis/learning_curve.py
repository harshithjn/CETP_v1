import itertools
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


def mape(y_true, y_pred):
    return float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100)


def machines_seen_axis(pairs_df, machines):
    results = []
    for k in [1, 2, 3, 4]:
        combo_mapes_learned = []
        combo_mapes_naive = []
        for combo in itertools.combinations(machines, k):
            train_df = pairs_df[pairs_df["dev_machine"].isin(combo)]
            test_df = pairs_df[~pairs_df["dev_machine"].isin(combo)]
            if len(train_df) == 0 or len(test_df) == 0:
                continue
            model = fit_p50_model(train_df)
            pred = model.predict(test_df[ALLOWED_FEATURES].to_numpy())
            learned_mape = mape(test_df["scaling_factor_p50"].to_numpy(), pred)
            naive_pred = 1.0 / test_df["bandwidth_ratio"].to_numpy()
            naive_mape = mape(test_df["scaling_factor_p50"].to_numpy(), naive_pred)
            combo_mapes_learned.append(learned_mape)
            combo_mapes_naive.append(naive_mape)
        results.append({
            "k": k,
            "n_train_rows": len(train_df),
            "n_test_rows": len(test_df),
            "n_combos": len(combo_mapes_learned),
            "learned_mean": float(np.mean(combo_mapes_learned)),
            "learned_min": float(np.min(combo_mapes_learned)),
            "learned_max": float(np.max(combo_mapes_learned)),
            "naive_mean": float(np.mean(combo_mapes_naive)),
            "naive_min": float(np.min(combo_mapes_naive)),
            "naive_max": float(np.max(combo_mapes_naive)),
        })
    return pd.DataFrame(results)


def data_density_axis(pairs_df, machines):
    n_queries = 21
    fractions = [0.25, 0.50, 0.75, 1.00]
    rng = np.random.default_rng(RANDOM_STATE)
    all_query_ids = sorted(pairs_df["query_id"].unique())

    results = []
    for frac in fractions:
        fold_mapes_learned = []
        fold_mapes_naive = []
        n_sample = max(2, int(round(frac * n_queries)))
        for held_out in machines:
            seen = [m for m in machines if m != held_out]
            base_train_mask = pairs_df["dev_machine"].isin(seen) & pairs_df["prod_machine"].isin(seen)
            test_mask = (pairs_df["dev_machine"] == held_out) | (pairs_df["prod_machine"] == held_out)
            sampled_queries = rng.choice(all_query_ids, size=n_sample, replace=False)
            train_df = pairs_df[base_train_mask & pairs_df["query_id"].isin(sampled_queries)]
            test_df = pairs_df[test_mask]
            model = fit_p50_model(train_df)
            pred = model.predict(test_df[ALLOWED_FEATURES].to_numpy())
            learned_mape = mape(test_df["scaling_factor_p50"].to_numpy(), pred)
            naive_pred = 1.0 / test_df["bandwidth_ratio"].to_numpy()
            naive_mape = mape(test_df["scaling_factor_p50"].to_numpy(), naive_pred)
            fold_mapes_learned.append(learned_mape)
            fold_mapes_naive.append(naive_mape)
        results.append({
            "query_fraction": frac,
            "n_queries_sampled": n_sample,
            "n_folds": len(fold_mapes_learned),
            "learned_mean": float(np.mean(fold_mapes_learned)),
            "learned_min": float(np.min(fold_mapes_learned)),
            "learned_max": float(np.max(fold_mapes_learned)),
            "naive_mean": float(np.mean(fold_mapes_naive)),
            "naive_min": float(np.min(fold_mapes_naive)),
            "naive_max": float(np.max(fold_mapes_naive)),
        })
    return pd.DataFrame(results)


def plot_learning_curves(machines_df, density_df, save_path):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))

    ax = axes[0]
    ax.plot(machines_df["k"], machines_df["learned_mean"], marker="o", color="#1f77b4", label="learned quantile (p50)")
    ax.fill_between(machines_df["k"], machines_df["learned_min"], machines_df["learned_max"], color="#1f77b4", alpha=0.15)
    ax.plot(machines_df["k"], machines_df["naive_mean"], marker="s", color="#d62728", label="naive-linear (1/bandwidth_ratio)")
    ax.fill_between(machines_df["k"], machines_df["naive_min"], machines_df["naive_max"], color="#d62728", alpha=0.15)
    ax.set_xlabel("distinct dev machines seen in training (k)")
    ax.set_ylabel("MAPE on scaling_factor_p50 (%)")
    ax.set_title("Machines-seen learning curve")
    ax.set_xticks([1, 2, 3, 4])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=False)

    ax = axes[1]
    ax.plot(density_df["query_fraction"], density_df["learned_mean"], marker="o", color="#1f77b4", label="learned quantile (p50)")
    ax.fill_between(density_df["query_fraction"], density_df["learned_min"], density_df["learned_max"], color="#1f77b4", alpha=0.15)
    ax.plot(density_df["query_fraction"], density_df["naive_mean"], marker="s", color="#d62728", label="naive-linear (1/bandwidth_ratio)")
    ax.fill_between(density_df["query_fraction"], density_df["naive_min"], density_df["naive_max"], color="#d62728", alpha=0.15)
    ax.set_xlabel("fraction of 21 queries used in training")
    ax.set_ylabel("MAPE on scaling_factor_p50 (%)")
    ax.set_title("Data-density learning curve (leave-one-machine-out)")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=False)

    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    pairs_df, machines = build_pairs_table()

    print("=" * 78)
    print("MACHINES-SEEN LEARNING CURVE")
    print("=" * 78)
    machines_df = machines_seen_axis(pairs_df, machines)
    print(machines_df.to_string(index=False))

    print("\n" + "=" * 78)
    print("DATA-DENSITY LEARNING CURVE (leave-one-machine-out)")
    print("=" * 78)
    density_df = data_density_axis(pairs_df, machines)
    print(density_df.to_string(index=False))

    machines_df.to_csv(RESULTS_DIR / "learning_curve_machines_seen.csv", index=False)
    density_df.to_csv(RESULTS_DIR / "learning_curve_data_density.csv", index=False)
    plot_learning_curves(machines_df, density_df, RESULTS_DIR / "learning_curve.png")

    print(f"\nSaved: {RESULTS_DIR / 'learning_curve_machines_seen.csv'}")
    print(f"Saved: {RESULTS_DIR / 'learning_curve_data_density.csv'}")
    print(f"Saved figure: {RESULTS_DIR / 'learning_curve.png'}")

    k1 = machines_df.iloc[0]
    k4 = machines_df.iloc[-1]
    print("\n" + "=" * 78)
    print("READ")
    print("=" * 78)
    machines_trend = "IMPROVES" if k4["learned_mean"] < k1["learned_mean"] - 1.0 else "DOES NOT clearly improve"
    print(f"Learned model MAPE at k=1: {k1['learned_mean']:.2f}% (range {k1['learned_min']:.2f}-{k1['learned_max']:.2f}%)")
    print(f"Learned model MAPE at k=4: {k4['learned_mean']:.2f}% (range {k4['learned_min']:.2f}-{k4['learned_max']:.2f}%)")
    print(f"Machines-seen trend: accuracy {machines_trend} as more dev machines are added to training.")

    f25 = density_df.iloc[0]
    f100 = density_df.iloc[-1]
    density_trend = "IMPROVES" if f100["learned_mean"] < f25["learned_mean"] - 1.0 else "DOES NOT clearly improve"
    print(f"\nLearned model MAPE at 25% queries: {f25['learned_mean']:.2f}%")
    print(f"Learned model MAPE at 100% queries: {f100['learned_mean']:.2f}%")
    print(f"Data-density trend: accuracy {density_trend} as more queries per machine are added to training.")

    gap_k1 = k1["learned_mean"] - k1["naive_mean"]
    gap_k4 = k4["learned_mean"] - k4["naive_mean"]
    gap_trend = "closes" if abs(gap_k4) < abs(gap_k1) - 1.0 else "does not close"
    print(f"\nGap to naive-linear at k=1: {gap_k1:+.2f}pp   at k=4: {gap_k4:+.2f}pp   -> gap {gap_trend} with more machines.")
