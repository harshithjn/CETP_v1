import json
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor

PROJECT_DIR = Path("/Users/harshithj/Main/Resources/CETP")
CSV_PATH = PROJECT_DIR / "tpch_dataset.csv"
MODELS_DIR = PROJECT_DIR / "models"
RESULTS_DIR = PROJECT_DIR / "results"
MODELS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

RANDOM_STATE = 42
SLA_SCALING_THRESHOLD = 1.5
COMPUTE_GATE_THRESHOLD = 0.5
N_BOOTSTRAP = 2000

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

pairs_df["dev_bandwidth"] = pairs_df["dev_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["bandwidth"])
pairs_df["prod_bandwidth"] = pairs_df["prod_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["bandwidth"])
pairs_df["dev_compute"] = pairs_df["dev_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["compute"])
pairs_df["prod_compute"] = pairs_df["prod_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["compute"])
pairs_df["bandwidth_ratio"] = pairs_df["prod_bandwidth"] / pairs_df["dev_bandwidth"]
pairs_df["compute_ratio"] = pairs_df["prod_compute"] / pairs_df["dev_compute"]

bottleneck_feature_cols = ["dev_cost", "dev_shared_hit", "dev_shared_read", "dev_total_buffers", "dev_io_ratio", "dev_rows"]
bottleneck_raw_cols = ["cost", "shared_hit", "shared_read", "total_buffers", "io_ratio", "rows"]
bottleneck_X = pairs_df[bottleneck_feature_cols].rename(columns=dict(zip(bottleneck_feature_cols, bottleneck_raw_cols)))[bottleneck_raw_cols]
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


def gated_scaling(row):
    if row["p_compute"] >= COMPUTE_GATE_THRESHOLD:
        return 1.0 / row["compute_ratio"]
    return 1.0 / row["bandwidth_ratio"]


pairs_df["analytical_scaling"] = pairs_df.apply(analytical_scaling, axis=1)
pairs_df["gated_scaling"] = pairs_df.apply(gated_scaling, axis=1)

print("=" * 78)
print("TASK A: DIAGNOSING THE COMPUTE SIGNAL")
print("=" * 78)
compute_queries = pairs_df[pairs_df["bottleneck_class"] == "compute"]
bandwidth_queries = pairs_df[pairs_df["bottleneck_class"] == "bandwidth"]
io_queries = pairs_df[pairs_df["bottleneck_class"] == "io"]
mixed_queries = pairs_df[pairs_df["bottleneck_class"] == "mixed"]

pooled_corr = pairs_df["compute_ratio"].corr(pairs_df["scaling_factor_p50"])
compute_corr = compute_queries["compute_ratio"].corr(compute_queries["scaling_factor_p50"])
bandwidth_corr = bandwidth_queries["compute_ratio"].corr(bandwidth_queries["scaling_factor_p50"])
io_corr = io_queries["compute_ratio"].corr(io_queries["scaling_factor_p50"])
mixed_corr = mixed_queries["compute_ratio"].corr(mixed_queries["scaling_factor_p50"])

pooled_bw_corr = pairs_df["bandwidth_ratio"].corr(pairs_df["scaling_factor_p50"])
compute_bw_corr = compute_queries["bandwidth_ratio"].corr(compute_queries["scaling_factor_p50"])

print(f"compute_ratio vs scaling_factor_p50, pooled (n={len(pairs_df)}):      {pooled_corr:.3f}")
print(f"compute_ratio vs scaling_factor_p50, compute queries (n={len(compute_queries)}):  {compute_corr:.3f}")
print(f"compute_ratio vs scaling_factor_p50, bandwidth queries (n={len(bandwidth_queries)}): {bandwidth_corr:.3f}")
print(f"compute_ratio vs scaling_factor_p50, io queries (n={len(io_queries)}):        {io_corr:.3f}")
print(f"compute_ratio vs scaling_factor_p50, mixed queries (n={len(mixed_queries)}):      {mixed_corr:.3f}")
print(f"\nbandwidth_ratio vs scaling_factor_p50, pooled:          {pooled_bw_corr:.3f}")
print(f"bandwidth_ratio vs scaling_factor_p50, compute queries: {compute_bw_corr:.3f}")

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
            loss="quantile", alpha=alpha, n_estimators=200, max_depth=3,
            learning_rate=0.05, random_state=RANDOM_STATE,
        )
        model.fit(X_train, train_df[target_col].to_numpy())
        quantile_preds[alpha] = model.predict(X_test)

    fold_out = test_df[[
        "dev_machine", "prod_machine", "query_id", "query_num", "bottleneck_class",
        "scaling_factor_p50", "scaling_factor_p95", "scaling_factor_p99",
        "bandwidth_ratio", "analytical_scaling", "gated_scaling",
    ]].copy()
    fold_out["held_out_machine"] = held_out
    fold_out["naive_pred"] = 1.0
    fold_out["naive_linear_pred"] = 1.0 / fold_out["bandwidth_ratio"]
    fold_out["analytical_pred"] = fold_out["analytical_scaling"]
    fold_out["gated_pred"] = fold_out["gated_scaling"]
    fold_out["learned_p50"] = quantile_preds[0.5]
    fold_out["learned_p95"] = quantile_preds[0.95]
    fold_out["learned_p99"] = quantile_preds[0.99]
    fold_predictions.append(fold_out)

cv_results = pd.concat(fold_predictions, ignore_index=True)


def mape_vec(y_true, y_pred):
    return np.abs((y_true - y_pred) / y_true) * 100


def mape(y_true, y_pred):
    return float(np.mean(mape_vec(y_true, y_pred)))


approach_cols = {
    "naive (=1.0)": "naive_pred",
    "naive-linear (1/bandwidth_ratio)": "naive_linear_pred",
    "analytical roofline": "analytical_pred",
    "bottleneck-gated (compute/bandwidth)": "gated_pred",
    "learned quantile (p50)": "learned_p50",
}

print("\nFlat single-ratio vs bottleneck-gated formula, LOMO CV MAPE on scaling_factor_p50:")
flat_mape = mape(cv_results["scaling_factor_p50"].to_numpy(), cv_results["naive_linear_pred"].to_numpy())
gated_mape = mape(cv_results["scaling_factor_p50"].to_numpy(), cv_results["gated_pred"].to_numpy())
print(f"  naive-linear (flat bandwidth_ratio): {flat_mape:.2f}%")
print(f"  bottleneck-gated (threshold p_compute>={COMPUTE_GATE_THRESHOLD}): {gated_mape:.2f}%")
gate_verdict = "YES, gated formula beats flat single-ratio" if gated_mape < flat_mape else "NO, gated formula does not beat flat single-ratio"
print(f"  VERDICT: {gate_verdict}")

n_gated_to_compute = int((pairs_df["p_compute"] >= COMPUTE_GATE_THRESHOLD).sum())
print(f"  ({n_gated_to_compute} of {len(pairs_df)} pairs routed to compute_ratio under this gate)")

print("\n" + "=" * 78)
print("TASK B: BOOTSTRAP CONFIDENCE INTERVALS (cluster bootstrap over machines)")
print("=" * 78)

rng = np.random.default_rng(RANDOM_STATE)


def cluster_bootstrap_mape(df, pred_col, machines_list, n_boot, rng):
    stats = np.empty(n_boot)
    machine_groups = {m: df[df["held_out_machine"] == m] for m in machines_list}
    for b in range(n_boot):
        sampled_machines = rng.choice(machines_list, size=len(machines_list), replace=True)
        sample = pd.concat([machine_groups[m] for m in sampled_machines], ignore_index=True)
        stats[b] = mape(sample["scaling_factor_p50"].to_numpy(), sample[pred_col].to_numpy())
    return stats


def cluster_bootstrap_diff(df, col_a, col_b, machines_list, n_boot, rng):
    stats = np.empty(n_boot)
    machine_groups = {m: df[df["held_out_machine"] == m] for m in machines_list}
    for b in range(n_boot):
        sampled_machines = rng.choice(machines_list, size=len(machines_list), replace=True)
        sample = pd.concat([machine_groups[m] for m in sampled_machines], ignore_index=True)
        mape_a = mape(sample["scaling_factor_p50"].to_numpy(), sample[col_a].to_numpy())
        mape_b = mape(sample["scaling_factor_p50"].to_numpy(), sample[col_b].to_numpy())
        stats[b] = mape_a - mape_b
    return stats


bootstrap_summary = []
print(f"{'approach':<38}{'point MAPE %':>14}{'95% CI low':>12}{'95% CI high':>14}")
for name, col in approach_cols.items():
    point = mape(cv_results["scaling_factor_p50"].to_numpy(), cv_results[col].to_numpy())
    boot = cluster_bootstrap_mape(cv_results, col, machines, N_BOOTSTRAP, rng)
    ci_low, ci_high = np.percentile(boot, [2.5, 97.5])
    print(f"{name:<38}{point:>14.2f}{ci_low:>12.2f}{ci_high:>14.2f}")
    bootstrap_summary.append({"approach": name, "mape": point, "ci_low": ci_low, "ci_high": ci_high})

print("\nPaired bootstrap: naive-linear MAPE minus learned-model MAPE (negative = naive-linear better)")
diff_learned = cluster_bootstrap_diff(cv_results, "naive_linear_pred", "learned_p50", machines, N_BOOTSTRAP, rng)
diff_ci = np.percentile(diff_learned, [2.5, 97.5])
point_diff = mape(cv_results["scaling_factor_p50"].to_numpy(), cv_results["naive_linear_pred"].to_numpy()) - \
             mape(cv_results["scaling_factor_p50"].to_numpy(), cv_results["learned_p50"].to_numpy())
print(f"  point estimate: {point_diff:.2f} pp,  95% CI: [{diff_ci[0]:.2f}, {diff_ci[1]:.2f}] pp")
significant_vs_learned = not (diff_ci[0] <= 0 <= diff_ci[1])
print(f"  95% CI {'excludes' if significant_vs_learned else 'includes'} zero -> "
      f"{'naive-linear significantly beats learned model' if significant_vs_learned else 'difference is within noise, not statistically significant'}")

print("\nPaired bootstrap: naive-linear MAPE minus analytical-roofline MAPE")
diff_analytical = cluster_bootstrap_diff(cv_results, "naive_linear_pred", "analytical_pred", machines, N_BOOTSTRAP, rng)
diff_ci_a = np.percentile(diff_analytical, [2.5, 97.5])
point_diff_a = mape(cv_results["scaling_factor_p50"].to_numpy(), cv_results["naive_linear_pred"].to_numpy()) - \
               mape(cv_results["scaling_factor_p50"].to_numpy(), cv_results["analytical_pred"].to_numpy())
print(f"  point estimate: {point_diff_a:.2f} pp,  95% CI: [{diff_ci_a[0]:.2f}, {diff_ci_a[1]:.2f}] pp")
significant_vs_analytical = not (diff_ci_a[0] <= 0 <= diff_ci_a[1])
print(f"  95% CI {'excludes' if significant_vs_analytical else 'includes'} zero -> "
      f"{'naive-linear significantly beats analytical' if significant_vs_analytical else 'difference is within noise, not statistically significant'}")

print("\n" + "=" * 78)
print("TASK C: THREE-STATE SLA GATE (pass / warn / block)")
print("=" * 78)


def gate_state(row):
    if row["analytical_pred"] > SLA_SCALING_THRESHOLD:
        return "block"
    if row["learned_p99"] <= SLA_SCALING_THRESHOLD:
        return "pass"
    return "warn"


cv_results["true_breach"] = cv_results["scaling_factor_p99"] > SLA_SCALING_THRESHOLD
cv_results["gate_state"] = cv_results.apply(gate_state, axis=1)

print(f"SLA threshold: scaling_factor_p99 > {SLA_SCALING_THRESHOLD}")
print(f"True breach rate overall: {cv_results['true_breach'].mean()*100:.1f}% ({cv_results['true_breach'].sum()}/{len(cv_results)})\n")
print(f"{'state':<10}{'n':>8}{'% of total':>14}{'actual breach rate in state':>30}")
gate_summary = []
for state in ["block", "warn", "pass"]:
    sub = cv_results[cv_results["gate_state"] == state]
    breach_rate = sub["true_breach"].mean() * 100 if len(sub) else float("nan")
    print(f"{state:<10}{len(sub):>8}{100*len(sub)/len(cv_results):>13.1f}%{breach_rate:>29.1f}%")
    gate_summary.append({"state": state, "n": len(sub), "pct_of_total": 100*len(sub)/len(cv_results), "actual_breach_rate_pct": breach_rate})

block_error = float(cv_results[cv_results["gate_state"] == "block"]["true_breach"].eq(False).mean() * 100) if (cv_results["gate_state"] == "block").any() else float("nan")
pass_error = float(cv_results[cv_results["gate_state"] == "pass"]["true_breach"].eq(True).mean() * 100) if (cv_results["gate_state"] == "pass").any() else float("nan")
print(f"\nBLOCK-state error rate (blocked pairs that were NOT actually a breach): {block_error:.1f}%")
print(f"PASS-state error rate (passed pairs that WERE actually a breach):       {pass_error:.1f}%")
print("WARN-state is deliberately non-committal: its actual-breach-rate above is the argument for deferring to a real prod run.")

print("\n" + "=" * 78)
print("PER-MACHINE / PER-CLASS DIAGNOSTIC (learned model, for the c5a finding)")
print("=" * 78)
print(f"{'held-out machine':<20}{'n':>8}{'learned MAPE %':>18}{'analytical MAPE %':>20}{'naive-linear MAPE %':>22}")
per_machine_rows = []
for m in machines:
    sub = cv_results[cv_results["held_out_machine"] == m]
    row = {
        "machine": m,
        "n": len(sub),
        "learned_mape": mape(sub["scaling_factor_p50"].to_numpy(), sub["learned_p50"].to_numpy()),
        "analytical_mape": mape(sub["scaling_factor_p50"].to_numpy(), sub["analytical_pred"].to_numpy()),
        "naive_linear_mape": mape(sub["scaling_factor_p50"].to_numpy(), sub["naive_linear_pred"].to_numpy()),
    }
    print(f"{m:<20}{row['n']:>8}{row['learned_mape']:>18.2f}{row['analytical_mape']:>20.2f}{row['naive_linear_mape']:>22.2f}")
    per_machine_rows.append(row)

final_table = pd.DataFrame(bootstrap_summary)
final_table.to_csv(RESULTS_DIR / "phase7_mape_bootstrap_table.csv", index=False)

gate_table = pd.DataFrame(gate_summary)
gate_table.to_csv(RESULTS_DIR / "phase7_gate_evaluation.csv", index=False)

per_machine_table = pd.DataFrame(per_machine_rows)
per_machine_table.to_csv(RESULTS_DIR / "phase7_per_machine_breakdown.csv", index=False)

compute_diag = pd.DataFrame([
    {"subset": "pooled", "n": len(pairs_df), "compute_ratio_corr": pooled_corr, "bandwidth_ratio_corr": pooled_bw_corr},
    {"subset": "compute queries", "n": len(compute_queries), "compute_ratio_corr": compute_corr, "bandwidth_ratio_corr": compute_bw_corr},
    {"subset": "bandwidth queries", "n": len(bandwidth_queries), "compute_ratio_corr": bandwidth_corr, "bandwidth_ratio_corr": None},
    {"subset": "io queries", "n": len(io_queries), "compute_ratio_corr": io_corr, "bandwidth_ratio_corr": None},
    {"subset": "mixed queries", "n": len(mixed_queries), "compute_ratio_corr": mixed_corr, "bandwidth_ratio_corr": None},
])
compute_diag.to_csv(RESULTS_DIR / "phase7_compute_signal_diagnosis.csv", index=False)

with open(MODELS_DIR / "phase7_gate_config.json", "w") as f:
    json.dump({
        "sla_scaling_threshold": SLA_SCALING_THRESHOLD,
        "compute_gate_threshold": COMPUTE_GATE_THRESHOLD,
        "gate_logic": "block if analytical_pred > threshold; else pass if learned_p99 <= threshold; else warn",
    }, f, indent=2)

print(f"\nSaved: {RESULTS_DIR / 'phase7_mape_bootstrap_table.csv'}")
print(f"Saved: {RESULTS_DIR / 'phase7_gate_evaluation.csv'}")
print(f"Saved: {RESULTS_DIR / 'phase7_per_machine_breakdown.csv'}")
print(f"Saved: {RESULTS_DIR / 'phase7_compute_signal_diagnosis.csv'}")
print(f"Saved: {MODELS_DIR / 'phase7_gate_config.json'}")
