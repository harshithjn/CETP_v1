import numpy as np
import pandas as pd
from pathlib import Path
import addition4_reevaluate as a4

RESULTS_DIR = Path("/Users/harshithj/Main/Resources/CETP/results")

VCPU_COUNT = {"c5a": 4, "m5a": 4, "r5n": 2, "z1d": 2, "c7i": 2}


def mape_series(y_true, y_pred):
    return np.abs((y_true - y_pred) / y_true) * 100


def per_query_mape(pairs_df, pred_col):
    pairs_df = pairs_df.copy()
    pairs_df["err"] = mape_series(pairs_df["scaling_factor_p50"], pairs_df[pred_col])
    return pairs_df.groupby("query_num")["err"].mean()


old_pairs, machines = a4.build_pairs(a4.OLD_COMPUTE)
new_pairs, _ = a4.build_pairs(a4.NEW_COMPUTE_MULTI)
vcpu_pairs, _ = a4.build_pairs(VCPU_COUNT)

mape_old = per_query_mape(old_pairs, "analytical_scaling")
mape_new = per_query_mape(new_pairs, "analytical_scaling")
mape_vcpu = per_query_mape(vcpu_pairs, "analytical_scaling")

benefit = (mape_old - mape_new).rename("benefit_multicore_vs_old")

parallel_df = pd.read_csv(RESULTS_DIR / "addition4b_parallel_plan_check.csv")
parallel_df = parallel_df.set_index("query_num")

report = pd.DataFrame({
    "mape_old_single_core": mape_old,
    "mape_new_multi_core": mape_new,
    "benefit": benefit,
    "uses_parallel": parallel_df["uses_parallel"],
    "workers_launched": parallel_df["workers_launched"],
}).sort_values("benefit", ascending=False)

print("=" * 78)
print("PER-QUERY: DOES MULTI-CORE COMPUTE SIGNAL HELP, RANKED, VS PARALLEL-PLAN STATUS")
print("=" * 78)
print(report.to_string())

parallel_group = report[report["uses_parallel"] == True]["benefit"]
serial_group = report[report["uses_parallel"] == False]["benefit"]
print(f"\nMean benefit, PARALLEL-plan queries (n={len(parallel_group)}): {parallel_group.mean():.2f} pp MAPE reduction")
print(f"Mean benefit, SERIAL-plan queries (n={len(serial_group)}): {serial_group.mean():.2f} pp MAPE reduction")
print(f"Serial-plan queries individually: {dict(serial_group)}")

point_biserial = report["benefit"].corr(report["uses_parallel"].astype(float))
print(f"\nPoint-biserial correlation(benefit, uses_parallel) across all 21 queries: {point_biserial:.3f}")
print("(Only 2 of 21 queries are non-parallel, so this correlation has very limited statistical power")
print(" — treat the group-mean comparison above as the primary evidence, this coefficient as secondary.)")

if serial_group.mean() < parallel_group.mean() - 1.0:
    verdict_a = "CONSISTENT with the parallelism mechanism: the two serial-plan queries benefited less from the multi-core signal than the parallel-plan queries did, on average."
elif abs(serial_group.mean() - parallel_group.mean()) <= 1.0:
    verdict_a = "AMBIGUOUS: serial-plan and parallel-plan queries show similar average benefit from the multi-core signal; the small serial-plan sample (n=2) limits what can be concluded."
else:
    verdict_a = "INCONSISTENT with the parallelism mechanism: the serial-plan queries benefited MORE than the parallel-plan queries did, which the parallelism hypothesis does not predict."
print(f"\nMECHANISM TEST VERDICT: {verdict_a}")

print("\n" + "=" * 78)
print("CONFOUND CHECK: MEASURED MULTI-CORE THROUGHPUT VS RAW VCPU COUNT")
print("=" * 78)

corr_measured = new_pairs["compute_ratio"].corr(new_pairs["scaling_factor_p50"])
corr_vcpu = vcpu_pairs["compute_ratio"].corr(vcpu_pairs["scaling_factor_p50"])
print(f"correlation(scaling_factor_p50, compute_ratio) using MEASURED multi-core GFLOPS: {corr_measured:.3f}")
print(f"correlation(scaling_factor_p50, compute_ratio) using RAW VCPU COUNT ratio:        {corr_vcpu:.3f}")

def lomo_mape(pairs_df, machines):
    lomo = a4.lomo_mape_table(pairs_df, machines)
    naive_linear = mape_series(lomo["scaling_factor_p50"], 1.0 / lomo["bandwidth_ratio"]).mean()
    analytical = mape_series(lomo["scaling_factor_p50"], lomo["analytical_scaling"]).mean()
    gated = mape_series(lomo["scaling_factor_p50"], lomo["gated_scaling"]).mean()
    return naive_linear, analytical, gated

naive_m, analytical_m, gated_m = lomo_mape(new_pairs, machines)
naive_v, analytical_v, gated_v = lomo_mape(vcpu_pairs, machines)

print(f"\nLOMO MAPE using MEASURED multi-core throughput: naive-linear={naive_m:.2f}%  analytical={analytical_m:.2f}%  gated={gated_m:.2f}%")
print(f"LOMO MAPE using RAW VCPU COUNT ratio:            naive-linear={naive_v:.2f}%  analytical={analytical_v:.2f}%  gated={gated_v:.2f}%")

if analytical_m < analytical_v - 0.5:
    verdict_b = "Measured throughput BEATS raw vCPU count as a predictor — the benchmark captures something beyond just core count."
elif abs(analytical_m - analytical_v) <= 0.5:
    verdict_b = "Measured throughput and raw vCPU count are EQUIVALENT predictors — the honest finding is that core count itself is the predictor, not measured throughput specifically."
else:
    verdict_b = "Raw vCPU count BEATS measured throughput — the benchmark adds noise relative to simply counting cores."
print(f"\nCONFOUND CHECK VERDICT: {verdict_b}")

summary = pd.DataFrame([{
    "mean_benefit_parallel_queries": parallel_group.mean(),
    "mean_benefit_serial_queries": serial_group.mean(),
    "point_biserial_corr": point_biserial,
    "corr_measured_throughput": corr_measured,
    "corr_vcpu_count": corr_vcpu,
    "analytical_mape_measured": analytical_m,
    "analytical_mape_vcpu": analytical_v,
}])
summary.to_csv(RESULTS_DIR / "addition4b_mechanism_summary.csv", index=False)
report.to_csv(RESULTS_DIR / "addition4b_per_query_benefit.csv")
print(f"\nSaved: {RESULTS_DIR / 'addition4b_mechanism_summary.csv'}")
print(f"Saved: {RESULTS_DIR / 'addition4b_per_query_benefit.csv'}")
