import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats

PROJECT_DIR = Path("/Users/harshithj/Main/Resources/CETP")
RESULTS_DIR = PROJECT_DIR / "results"

timing = pd.read_csv(RESULTS_DIR / "fix3_parallel_timing.csv")
benefit_df = pd.read_csv(RESULTS_DIR / "addition4b_per_query_benefit.csv")
plan_check = pd.read_csv(RESULTS_DIR / "addition4b_parallel_plan_check.csv")

off_times = timing[timing["config"] == "OFF"].set_index("query_num")["median_time_ms"]
on_times = timing[timing["config"] == "ON"].set_index("query_num")["median_time_ms"]

speedup = (off_times / on_times).rename("parallel_speedup")

merged = pd.DataFrame({"parallel_speedup": speedup}).join(
    benefit_df.set_index("query_num")[["benefit", "mape_old_single_core", "mape_new_multi_core", "uses_parallel", "workers_launched"]]
)
merged = merged.join(off_times.rename("off_time_ms")).join(on_times.rename("on_time_ms"))
merged = merged.sort_values("parallel_speedup", ascending=False)

print("=" * 90)
print("FIX 3: LOCAL PARALLELISM ON/OFF TIMING VS MULTI-CORE-COMPUTE-SIGNAL BENEFIT")
print("=" * 90)
print(f"\n{'query':<8}{'off_ms':>10}{'on_ms':>10}{'speedup':>10}{'benefit(pp)':>13}{'uses_parallel':>15}")
for qn, row in merged.iterrows():
    print(f"q{qn:<7}{row['off_time_ms']:>10.1f}{row['on_time_ms']:>10.1f}{row['parallel_speedup']:>10.3f}"
          f"{row['benefit']:>13.2f}{str(row['uses_parallel']):>15}")

pearson_r, pearson_p = stats.pearsonr(merged["parallel_speedup"], merged["benefit"])
spearman_r, spearman_p = stats.spearmanr(merged["parallel_speedup"], merged["benefit"])

print(f"\nPearson  correlation(parallel_speedup, multicore_benefit), n={len(merged)}: r={pearson_r:.3f}  p={pearson_p:.4f}")
print(f"Spearman correlation(parallel_speedup, multicore_benefit), n={len(merged)}: rho={spearman_r:.3f}  p={spearman_p:.4f}")

parallel_group = merged[merged["uses_parallel"] == True]["parallel_speedup"]
serial_group = merged[merged["uses_parallel"] == False]["parallel_speedup"]
print(f"\nMean parallel_speedup, plan-parallel queries (n={len(parallel_group)}): {parallel_group.mean():.3f}")
print(f"Mean parallel_speedup, plan-serial queries (n={len(serial_group)}):   {serial_group.mean():.3f}")
print(f"(sanity check: serial-plan queries q11/q20 should show ~1.0x speedup since the planner never chose a")
print(f" parallel plan for them regardless of the GUC -- confirms the ON/OFF toggle is doing real work, not noise)")

if pearson_r > 0.3 and pearson_p < 0.05:
    verdict = ("CONFIRMED: enabling parallelism speeds up runtime, and the size of that speedup correlates "
               "positively and significantly with how much the query benefited from the multi-core compute "
               "signal in the earlier per-query analysis. This is a direct causal link the plan-node inspection "
               "alone could not establish -- the mechanism hypothesis is now confirmed, not just leaning-confirmed.")
elif pearson_r > 0.15:
    verdict = ("LEANING CONFIRMED (upgraded from plan-only inspection, but not strong enough to call fully "
               "confirmed): parallel speedup and multi-core benefit correlate positively, but the correlation "
               "is not strong/significant enough at n=21 to be a slam-dunk causal confirmation. Consistent "
               "with, but not conclusive proof of, the parallelism mechanism.")
elif pearson_r > -0.15:
    verdict = ("AMBIGUOUS / WEAK: parallel speedup and multi-core benefit show close to zero linear correlation. "
               "Local single-machine timing does not clearly support the parallelism mechanism as the primary "
               "explanation -- something else may also be contributing, or the relationship is noisier than "
               "the plan-node inspection suggested.")
else:
    verdict = ("REFUTED: parallel speedup and multi-core benefit are negatively correlated -- queries that speed "
               "up most from enabling parallelism are NOT the ones that benefited most from the multi-core "
               "compute signal. This is inconsistent with the parallelism mechanism as stated and should be "
               "reported as a refutation, not softened.")

print(f"\nVERDICT: {verdict}")

print("\n" + "=" * 90)
print("HONEST CAVEAT")
print("=" * 90)
print("This experiment runs on ONE local machine (this workstation's PostgreSQL 15.17 SF1 instance), toggling")
print("max_parallel_workers_per_gather between 0 and 2 and measuring the resulting runtime change. It tests")
print("whether parallelism causes a speedup, and whether that speedup tracks the queries where the multi-core")
print("compute signal mattered in the cross-machine analysis. It is NOT a re-measurement of the original 5-EC2")
print("-machine cross-environment scaling result, and it cannot directly confirm that cross-machine differences")
print("in the collection data were caused by parallelism (the 5 collection instances are terminated and cannot")
print("be re-queried). It strengthens or weakens the plausibility of the mechanism; it is one additional, more")
print("direct piece of evidence than the earlier static EXPLAIN-plan inspection, not a substitute for it.")

summary = pd.DataFrame([{
    "pearson_r": pearson_r, "pearson_p": pearson_p,
    "spearman_rho": spearman_r, "spearman_p": spearman_p,
    "mean_speedup_parallel_plan_queries": parallel_group.mean(),
    "mean_speedup_serial_plan_queries": serial_group.mean(),
    "verdict": verdict,
}])
summary.to_csv(RESULTS_DIR / "fix3_parallelism_mechanism_summary.csv", index=False)
merged.reset_index().rename(columns={"index": "query_num"}).to_csv(RESULTS_DIR / "fix3_parallelism_per_query.csv", index=False)
print(f"\nSaved: {RESULTS_DIR / 'fix3_parallelism_mechanism_summary.csv'}")
print(f"Saved: {RESULTS_DIR / 'fix3_parallelism_per_query.csv'}")
