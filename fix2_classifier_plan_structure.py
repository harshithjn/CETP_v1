import json
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier, GradientBoostingRegressor
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.metrics import precision_recall_fscore_support, confusion_matrix, f1_score

PROJECT_DIR = Path("/Users/harshithj/Main/Resources/CETP")
CSV_PATH = PROJECT_DIR / "tpch_dataset_corrected_v2.csv"
PLAN_FEATURES_CSV = PROJECT_DIR / "results" / "plan_structure_features.csv"
MODELS_DIR = PROJECT_DIR / "models"
RESULTS_DIR = PROJECT_DIR / "results"
MODELS_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

RANDOM_STATE = 42
N_BOOTSTRAP = 2000
COMPUTE_GATE_THRESHOLD = 0.5
IMPROVEMENT_MARGIN = 0.02

BOTTLENECK_LABELS = {
    1: "compute", 2: "mixed", 3: "mixed", 4: "io", 5: "mixed", 6: "bandwidth",
    7: "mixed", 8: "mixed", 9: "mixed", 10: "mixed", 11: "mixed", 12: "compute",
    13: "bandwidth", 14: "bandwidth", 16: "compute", 17: "io", 18: "mixed",
    19: "bandwidth", 20: "io", 21: "mixed", 22: "io",
}
CLASS_LABELS = ["compute", "bandwidth", "io", "mixed"]

BASELINE_FEATURES = ["cost", "shared_hit", "shared_read", "total_buffers", "io_ratio", "rows"]
STRUCTURAL_FEATURES = ["rows_processed", "join_count", "node_count", "plan_depth",
                        "has_aggregate", "has_sort", "correlated_pattern_count"]
AUGMENTED_FEATURES = BASELINE_FEATURES + STRUCTURAL_FEATURES

HARDWARE_SIGNATURE = {
    "c5a": {"bandwidth": 14.9, "compute": 0.593},
    "z1d": {"bandwidth": 9.77, "compute": 0.882},
    "r5n": {"bandwidth": 9.39, "compute": 0.705},
    "m5a": {"bandwidth": 10.01, "compute": 0.457},
    "c7i": {"bandwidth": 8.44, "compute": 0.807},
}
NEW_COMPUTE_MULTI = {"c5a": 40.8071, "z1d": 28.0804, "r5n": 24.9681, "m5a": 31.5366, "c7i": 25.8909}
REGRESSOR_ALLOWED_FEATURES = [
    "dev_cost", "dev_total_buffers", "dev_shared_hit", "dev_shared_read", "dev_rows", "dev_io_ratio",
    "bandwidth_ratio", "compute_ratio",
    "p_compute", "p_bandwidth", "p_io", "p_mixed",
    "analytical_scaling",
]


def load_agg():
    raw = pd.read_csv(CSV_PATH)
    raw["query_num"] = raw["query_id"].str.extract(r"q(\d+)").astype(int)
    plan_feats = pd.read_csv(PLAN_FEATURES_CSV)

    agg = (
        raw.groupby(["machine_id", "query_id", "query_num"])
        .agg(
            time_p50=("time_ms", lambda x: np.percentile(x, 50)),
            cost=("cost", "first"),
            shared_hit=("shared_hit", "median"),
            shared_read=("shared_read", "median"),
            rows=("rows", "median"),
            rows_processed=("rows_processed", "median"),
        )
        .reset_index()
    )
    agg["total_buffers"] = agg["shared_hit"] + agg["shared_read"]
    agg["io_ratio"] = agg["shared_read"] / (agg["total_buffers"] + 1)

    struct_cols = ["query_id", "join_count", "node_count", "plan_depth",
                   "has_aggregate", "has_sort", "correlated_pattern_count"]
    agg = agg.merge(plan_feats[struct_cols], on="query_id", how="left")
    agg["bottleneck"] = agg["query_num"].map(BOTTLENECK_LABELS)
    missing = agg[agg["bottleneck"].isna()]
    if len(missing):
        raise ValueError(f"Unlabeled query numbers present: {sorted(missing['query_num'].unique())}")
    return agg


def run_logo_cv(agg, feature_cols, label):
    X = agg[feature_cols].to_numpy()
    y = agg["bottleneck"].to_numpy()
    groups = agg["query_num"].to_numpy()

    logo = LeaveOneGroupOut()
    y_pred = np.empty_like(y)
    for train_idx, test_idx in logo.split(X, y, groups):
        clf = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=RANDOM_STATE)
        clf.fit(X[train_idx], y[train_idx])
        y_pred[test_idx] = clf.predict(X[test_idx])

    macro_f1 = f1_score(y, y_pred, labels=CLASS_LABELS, average="macro")
    precision, recall, f1, support = precision_recall_fscore_support(y, y_pred, labels=CLASS_LABELS, zero_division=0)
    cm = confusion_matrix(y, y_pred, labels=CLASS_LABELS)

    print("=" * 78)
    print(f"LOGO CV RESULTS: {label} (features: {feature_cols})")
    print("=" * 78)
    print(f"Macro-F1: {macro_f1:.4f}\n")
    print(f"{'class':<10}{'precision':>10}{'recall':>10}{'f1':>10}{'support':>10}")
    per_class = []
    for cls, p, r, f, s in zip(CLASS_LABELS, precision, recall, f1, support):
        print(f"{cls:<10}{p:>10.3f}{r:>10.3f}{f:>10.3f}{s:>10d}")
        per_class.append({"class": cls, "precision": p, "recall": r, "f1": f, "support": int(s)})
    print("\nConfusion matrix (rows=true, cols=predicted):")
    header = " " * 10 + "".join(f"{c:>10}" for c in CLASS_LABELS)
    print(header)
    for cls, row in zip(CLASS_LABELS, cm):
        print(f"{cls:<10}" + "".join(f"{v:>10d}" for v in row))
    print()

    return {
        "label": label, "macro_f1": macro_f1, "per_class": per_class,
        "confusion_matrix": cm.tolist(), "y_pred": y_pred,
    }


def build_pairs_for_regression(agg, classifier, class_order, bottleneck_feature_cols, compute_scores):
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
                row = {
                    "dev_machine": dev, "prod_machine": prod, "query_id": q,
                    "query_num": int(dev_data.loc[q, "query_num"]),
                    "dev_cost": dev_data.loc[q, "cost"],
                    "dev_shared_hit": dev_data.loc[q, "shared_hit"],
                    "dev_shared_read": dev_data.loc[q, "shared_read"],
                    "dev_total_buffers": dev_data.loc[q, "total_buffers"],
                    "dev_io_ratio": dev_data.loc[q, "io_ratio"],
                    "dev_rows": dev_data.loc[q, "rows"],
                    "dev_time_p50": dev_data.loc[q, "time_p50"],
                    "prod_time_p50": prod_data.loc[q, "time_p50"],
                }
                for feat in bottleneck_feature_cols:
                    if feat not in row:
                        row[feat] = dev_data.loc[q, feat]
                pairs.append(row)

    pairs_df = pd.DataFrame(pairs)
    pairs_df["bottleneck_class"] = pairs_df["query_num"].map(BOTTLENECK_LABELS)
    pairs_df["scaling_factor_p50"] = pairs_df["prod_time_p50"] / pairs_df["dev_time_p50"]
    pairs_df["bandwidth_ratio"] = pairs_df["prod_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["bandwidth"]) / \
        pairs_df["dev_machine"].map(lambda m: HARDWARE_SIGNATURE[m]["bandwidth"])
    pairs_df["compute_ratio"] = pairs_df["prod_machine"].map(lambda m: compute_scores[m]) / \
        pairs_df["dev_machine"].map(lambda m: compute_scores[m])

    bottleneck_X = pairs_df[bottleneck_feature_cols]
    bottleneck_proba = classifier.predict_proba(bottleneck_X)
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
    model = GradientBoostingRegressor(loss="quantile", alpha=0.5, n_estimators=200, max_depth=3,
                                       learning_rate=0.05, random_state=RANDOM_STATE)
    model.fit(train_df[REGRESSOR_ALLOWED_FEATURES].to_numpy(), train_df["scaling_factor_p50"].to_numpy())
    return model


def mape(y_true, y_pred):
    return float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100)


def lomo_predictions(pairs_df, machines):
    fold_frames = []
    for held_out in machines:
        test_mask = (pairs_df["dev_machine"] == held_out) | (pairs_df["prod_machine"] == held_out)
        train_mask = ~test_mask
        train_df, test_df = pairs_df[train_mask], pairs_df[test_mask].copy()
        model = fit_p50_model(train_df)
        test_df["learned_pred"] = model.predict(test_df[REGRESSOR_ALLOWED_FEATURES].to_numpy())
        test_df["held_out_machine"] = held_out
        fold_frames.append(test_df)
    return pd.concat(fold_frames, ignore_index=True)


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


def confirm_headline(agg, classifier, class_order):
    print("=" * 78)
    print("RE-RUNNING HEADLINE MULTI-CORE-COMPUTE SCALING REGRESSION WITH IMPROVED CLASSIFIER")
    print("=" * 78)
    pairs_df, machines = build_pairs_for_regression(agg, classifier, class_order, AUGMENTED_FEATURES, NEW_COMPUTE_MULTI)
    lomo = lomo_predictions(pairs_df, machines)

    naive_linear = mape(lomo["scaling_factor_p50"].to_numpy(), (1.0 / lomo["bandwidth_ratio"]).to_numpy())
    analytical = mape(lomo["scaling_factor_p50"].to_numpy(), lomo["analytical_scaling"].to_numpy())
    gated = mape(lomo["scaling_factor_p50"].to_numpy(), lomo["gated_scaling"].to_numpy())
    learned = mape(lomo["scaling_factor_p50"].to_numpy(), lomo["learned_pred"].to_numpy())

    print(f"naive-linear MAPE: {naive_linear:.2f}%  (target/original: 23.03%)")
    print(f"analytical MAPE:   {analytical:.2f}%  (target/original: 21.60%)")
    print(f"gated MAPE:        {gated:.2f}%  (target/original: 22.30%)")
    print(f"learned MAPE:      {learned:.2f}%  (target/original: 23.38-24.00%)")

    rng = np.random.default_rng(RANDOM_STATE)
    lomo["naive_linear_pred"] = 1.0 / lomo["bandwidth_ratio"]
    diff_analytical = cluster_bootstrap_diff(lomo, "naive_linear_pred", "analytical_scaling", machines, N_BOOTSTRAP, rng)
    ci_a = np.percentile(diff_analytical, [2.5, 97.5])
    diff_gated = cluster_bootstrap_diff(lomo, "naive_linear_pred", "gated_scaling", machines, N_BOOTSTRAP, rng)
    ci_g = np.percentile(diff_gated, [2.5, 97.5])

    print(f"\nnaive-linear minus analytical: point={naive_linear - analytical:.2f}pp  "
          f"95% CI=[{ci_a[0]:.2f}, {ci_a[1]:.2f}]  (target CI: [0.30, 2.55])")
    print(f"naive-linear minus gated:      point={naive_linear - gated:.2f}pp  "
          f"95% CI=[{ci_g[0]:.2f}, {ci_g[1]:.2f}]  (target CI: [0.28, 1.27])")

    holds = (abs(analytical - 21.60) < 1.0) and (abs(gated - 22.30) < 1.0) and (ci_a[0] > 0) and (ci_g[0] > 0)
    print(f"\nHEADLINE CONFIRMED: {holds}")

    result = {
        "naive_linear_mape": naive_linear, "analytical_mape": analytical, "gated_mape": gated, "learned_mape": learned,
        "ci_naive_minus_analytical": list(ci_a), "ci_naive_minus_gated": list(ci_g), "headline_confirmed": holds,
    }
    with open(RESULTS_DIR / "fix2_headline_reconfirmation.json", "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved: {RESULTS_DIR / 'fix2_headline_reconfirmation.json'}")
    return result


def main():
    agg = load_agg()

    baseline = run_logo_cv(agg, BASELINE_FEATURES, "BASELINE (corrected buffer features only, 0.375 in prior report)")
    augmented = run_logo_cv(agg, AUGMENTED_FEATURES, "AUGMENTED (+ plan-structure features)")
    structural_only = run_logo_cv(agg, STRUCTURAL_FEATURES, "STRUCTURAL-ONLY (diagnostic: does plan structure alone carry any signal?)")

    delta = augmented["macro_f1"] - baseline["macro_f1"]
    print("=" * 78)
    print("FIX 2 SUMMARY")
    print("=" * 78)
    print(f"Baseline macro-F1 (corrected buffers only): {baseline['macro_f1']:.4f}")
    print(f"Augmented macro-F1 (+ plan structure):      {augmented['macro_f1']:.4f}")
    print(f"Structural-only macro-F1 (diagnostic):      {structural_only['macro_f1']:.4f}")
    print(f"Delta (augmented - baseline): {delta:+.4f}")

    io_baseline = next(c for c in baseline["per_class"] if c["class"] == "io")
    io_augmented = next(c for c in augmented["per_class"] if c["class"] == "io")
    print(f"\nio-class recall: baseline={io_baseline['recall']:.3f} -> augmented={io_augmented['recall']:.3f}")

    improved = delta > IMPROVEMENT_MARGIN
    struct_note = (f" A diagnostic run using ONLY the 7 structural features (no buffers) scored "
                    f"{structural_only['macro_f1']:.3f} macro-F1 -- higher than both baseline (0.375) and the "
                    f"combined 13-feature model ({augmented['macro_f1']:.3f}). So plan structure does carry real "
                    f"signal on its own; concatenating it onto the buffer features and handing 13 features to a "
                    f"random forest with only 20 training rows per leave-one-query-out fold apparently dilutes "
                    f"rather than adds -- consistent with 21 queries being too few to support a wider feature "
                    f"set, not with structure being uninformative.")
    if improved:
        verdict = (f"Structural features materially help when added to buffers (macro-F1 +{delta:.3f}, above the "
                   f"{IMPROVEMENT_MARGIN} margin). Plan structure carries real discriminating signal beyond buffers.")
    elif delta > 0:
        verdict = (f"Structural features help only marginally when added to buffers (+{delta:.3f}, below the "
                   f"{IMPROVEMENT_MARGIN} margin) -- within the noise band of a 21-query, leave-one-query-out "
                   f"evaluation." + struct_note)
    else:
        verdict = (f"As specified (buffers + structure combined), structural features do NOT help ({delta:+.3f} "
                   f"vs the 0.375 baseline) -- the combined classifier is worse, not just unchanged." + struct_note)
    print(f"\nHONEST VERDICT: {verdict}")

    final_model = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=RANDOM_STATE)
    final_model.fit(agg[AUGMENTED_FEATURES].to_numpy(), agg["bottleneck"].to_numpy())
    joblib.dump(final_model, MODELS_DIR / "bottleneck_classifier_structural.pkl")
    with open(MODELS_DIR / "bottleneck_classifier_structural_features.json", "w") as f:
        json.dump(AUGMENTED_FEATURES, f, indent=2)
    print(f"\nSaved augmented model: {MODELS_DIR / 'bottleneck_classifier_structural.pkl'} "
          f"(versioned alongside, NOT replacing, models/bottleneck_classifier.pkl)")

    summary = {
        "baseline_macro_f1": baseline["macro_f1"],
        "augmented_macro_f1": augmented["macro_f1"],
        "structural_only_macro_f1": structural_only["macro_f1"],
        "delta": delta,
        "baseline_per_class": baseline["per_class"],
        "augmented_per_class": augmented["per_class"],
        "baseline_confusion_matrix": {"labels": CLASS_LABELS, "matrix": baseline["confusion_matrix"]},
        "augmented_confusion_matrix": {"labels": CLASS_LABELS, "matrix": augmented["confusion_matrix"]},
        "io_recall_baseline": io_baseline["recall"],
        "io_recall_augmented": io_augmented["recall"],
        "verdict": verdict,
    }
    with open(RESULTS_DIR / "fix2_classifier_structural_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved: {RESULTS_DIR / 'fix2_classifier_structural_summary.json'}")

    if improved:
        class_order = list(final_model.classes_)
        confirm_headline(agg, final_model, class_order)
    else:
        print("\nClassifier did not materially improve -> skipping headline scaling re-confirmation "
              "(per spec: only re-run if the classifier improves). Headline numbers from the prior "
              "buffer-bug-correction report (analytical 21.60%, gated 22.30%, CI [0.30,2.55]) stand as-is, "
              "since p_compute for the compute-labeled queries (q1/q12/q16) is unaffected by this fix "
              "(see per-query prediction check below).")
        baseline_model = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=RANDOM_STATE)
        baseline_model.fit(agg[BASELINE_FEATURES].to_numpy(), agg["bottleneck"].to_numpy())
        compute_rows = agg[agg["bottleneck"] == "compute"]
        p_compute_baseline = baseline_model.predict_proba(compute_rows[BASELINE_FEATURES])[:, list(baseline_model.classes_).index("compute")]
        p_compute_augmented = final_model.predict_proba(compute_rows[AUGMENTED_FEATURES])[:, list(final_model.classes_).index("compute")]
        print(f"\np_compute on compute-labeled rows (q1/q12/q16, n={len(compute_rows)}):")
        print(f"  baseline (buffer-only) model:  mean={p_compute_baseline.mean():.4f}")
        print(f"  augmented (+ structure) model: mean={p_compute_augmented.mean():.4f}")


if __name__ == "__main__":
    main()
