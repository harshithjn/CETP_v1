import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_DIR = Path("/Users/harshithj/Main/Resources/CETP")
BASE_CSV = PROJECT_DIR / "tpch_dataset_corrected.csv"
PLAN_FEATURES_CSV = PROJECT_DIR / "results" / "plan_structure_features.csv"
OUT_CSV = PROJECT_DIR / "tpch_dataset_corrected_v2.csv"
RESULTS_DIR = PROJECT_DIR / "results"

BOTTLENECK_LABELS = {
    1: "compute", 2: "mixed", 3: "mixed", 4: "io", 5: "mixed", 6: "bandwidth",
    7: "mixed", 8: "mixed", 9: "mixed", 10: "mixed", 11: "mixed", 12: "compute",
    13: "bandwidth", 14: "bandwidth", 16: "compute", 17: "io", 18: "mixed",
    19: "bandwidth", 20: "io", 21: "mixed", 22: "io",
}


def build_pairs(agg, rows_col):
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
                    "dev_rows": dev_data.loc[q, rows_col],
                    "dev_time_p50": dev_data.loc[q, "time_p50"],
                    "prod_time_p50": prod_data.loc[q, "time_p50"],
                })
    pairs_df = pd.DataFrame(pairs)
    pairs_df["scaling_factor_p50"] = pairs_df["prod_time_p50"] / pairs_df["dev_time_p50"]
    pairs_df["bottleneck_class"] = pairs_df["query_num"].map(BOTTLENECK_LABELS)
    return pairs_df


def main():
    raw = pd.read_csv(BASE_CSV)
    raw["query_num"] = raw["query_id"].str.extract(r"q(\d+)").astype(int)

    plan_feats = pd.read_csv(PLAN_FEATURES_CSV)[["query_id", "rows_processed"]]

    corrected_v2 = raw.merge(plan_feats, on="query_id", how="left")
    assert corrected_v2["rows_processed"].isna().sum() == 0, "missing rows_processed for some query_id"
    assert (corrected_v2["cost"].values == raw["cost"].values).all()
    assert (corrected_v2["time_ms"].values == raw["time_ms"].values).all()
    assert (corrected_v2["shared_hit"].values == raw["shared_hit"].values).all()
    assert (corrected_v2["shared_read"].values == raw["shared_read"].values).all()
    assert (corrected_v2["rows"].values == raw["rows"].values).all()
    corrected_v2 = corrected_v2.drop(columns=["query_num"])
    corrected_v2.to_csv(OUT_CSV, index=False)
    print(f"Wrote {OUT_CSV} ({len(corrected_v2)} rows, columns={list(corrected_v2.columns)})")
    print(f"tpch_dataset_corrected.csv left untouched.\n")

    agg = (
        raw.merge(plan_feats, on="query_id", how="left")
        .groupby(["machine_id", "query_id", "query_num"])
        .agg(
            time_p50=("time_ms", lambda x: np.percentile(x, 50)),
            rows_old=("rows", "median"),
            rows_processed=("rows_processed", "median"),
        )
        .reset_index()
    )

    old_pairs = build_pairs(agg, "rows_old")
    new_pairs = build_pairs(agg, "rows_processed")

    print("=" * 78)
    print("FIX 1: OLD 'rows' (top-node output) VS NEW 'rows_processed' (summed actual rows x loops)")
    print("=" * 78)

    corr_old_pooled = old_pairs["dev_rows"].corr(old_pairs["scaling_factor_p50"])
    corr_new_pooled = new_pairs["dev_rows"].corr(new_pairs["scaling_factor_p50"])
    print(f"\nPooled correlation (n={len(old_pairs)} dev/prod pairs) with scaling_factor_p50:")
    print(f"  old rows            : {corr_old_pooled:.4f}")
    print(f"  new rows_processed  : {corr_new_pooled:.4f}")

    print("\nPer-bottleneck-class correlation with scaling_factor_p50:")
    print(f"{'class':<12}{'n':>6}{'old rows':>12}{'rows_processed':>16}")
    class_rows = []
    for cls in ["compute", "bandwidth", "io", "mixed"]:
        sub_old = old_pairs[old_pairs["bottleneck_class"] == cls]
        sub_new = new_pairs[new_pairs["bottleneck_class"] == cls]
        c_old = sub_old["dev_rows"].corr(sub_old["scaling_factor_p50"])
        c_new = sub_new["dev_rows"].corr(sub_new["scaling_factor_p50"])
        print(f"{cls:<12}{len(sub_old):>6}{c_old:>12.4f}{c_new:>16.4f}")
        class_rows.append({"bottleneck_class": cls, "n": len(sub_old), "corr_old_rows": c_old, "corr_rows_processed": c_new})

    print(f"\nAbs magnitude comparison: |old|={abs(corr_old_pooled):.4f}  |new|={abs(corr_new_pooled):.4f}")
    if abs(corr_new_pooled) > abs(corr_old_pooled) + 0.02:
        verdict = "STRONGER: rows_processed has a larger-magnitude correlation with scaling_factor_p50 than the old top-node rows feature."
    elif abs(corr_new_pooled) < abs(corr_old_pooled) - 0.02:
        verdict = "WEAKER: rows_processed has a smaller-magnitude correlation with scaling_factor_p50 than the old top-node rows feature. The fix does not strengthen this particular correlation."
    else:
        verdict = "UNCHANGED: rows_processed and old rows correlate with scaling_factor_p50 at essentially the same (weak) magnitude. Rewriting rows-as-work-done does not, by itself, make this feature a stronger predictor of cross-machine scaling."
    print(f"\nVERDICT: {verdict}")
    print("\nNote: scaling_factor_p50 = prod_time / dev_time is a ratio between two machines running the SAME")
    print("query on the SAME data, so it is driven by hardware differences, not by how much work a query does.")
    print("rows_processed is a per-query, hardware-invariant quantity (identical for a query across all 5")
    print("machines). Correlating it against scaling_factor_p50 pools different queries together and tests")
    print("whether 'bigger/more-work queries scale differently across hardware' -- a real but indirect question;")
    print("rows/rows_processed is not expected to be a strong direct predictor of the cross-machine ratio itself.")
    print("Its intended use is as a bottleneck-classifier input (Fix 2), where within-query correctness matters")
    print("more than cross-query correlation with scaling_factor_p50.")

    summary = pd.DataFrame([{
        "corr_old_rows_pooled": corr_old_pooled,
        "corr_rows_processed_pooled": corr_new_pooled,
        "n_pairs": len(old_pairs),
        "verdict": verdict,
    }])
    summary.to_csv(RESULTS_DIR / "fix1_rows_processed_correlation.csv", index=False)
    pd.DataFrame(class_rows).to_csv(RESULTS_DIR / "fix1_rows_processed_correlation_by_class.csv", index=False)
    print(f"\nSaved: {RESULTS_DIR / 'fix1_rows_processed_correlation.csv'}")
    print(f"Saved: {RESULTS_DIR / 'fix1_rows_processed_correlation_by_class.csv'}")


if __name__ == "__main__":
    main()
