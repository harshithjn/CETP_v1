"""
arm64 laptop as the development machine (TASKS.md Tier 1, item 27).

Every dev->prod pair in the dataset is EC2->EC2. The product claim is laptop->production.
This tests it for the first time, using an Apple M1 (arm64, 8 cores, 8 GB) as dev and the
five EC2 instances as production.

Hardware signature for the M1, measured with the ORIGINAL probe and protocol
(compute_probe.c, gcc -O3 -march=native, 15 s target, seed 42) so it is directly
comparable to the EC2 figures:

    single-threaded      12.53 GFLOPS   (EC2 range 8.04 - 15.81  -> inside the hull)
    multi-core aggregate 61.82 GFLOPS   (EC2 range 24.97 - 40.81 -> 1.52x ABOVE the hull)

**The bandwidth axis cannot be used.** The STREAM-triad probe that produced the EC2
bandwidth figures (8.44 - 14.9) exists nowhere in the repository and cannot be re-run, so
no M1 bandwidth number on that scale can be produced. Any such number would be on an
unknown scale. This blocks every bandwidth-based predictor -- including `naive-linear`,
the project's headline BASELINE -- from being evaluated laptop->cloud at all.

What survives is `cmp_only` (predicted scaling = 1/compute_ratio), which needs only the
compute axis, whose probe is in the repo. That it is also the best-performing predictor
found in the ablation is convenient rather than planned.

Writes results/tier1_laptop_dev.csv and results/tier1_laptop_dev.md.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_evaluation import (  # noqa: E402
    COMPUTE_MULTI, RESULTS_DIR, build_pairs, load_cells, score_all,
)

PROJECT_DIR = Path(__file__).resolve().parents[2]
LAPTOP_CSV = PROJECT_DIR / "data" / "laptop" / "tpch_dataset_m1.csv"

M1_COMPUTE_MT = 61.8163   # 8 threads = nproc, matching the EC2 collection protocol
M1_COMPUTE_ST = 12.5315   # 1 thread

# The EC2 protocol ran compute_probe with `nproc` threads. But a PostgreSQL query can only
# use 1 + max_parallel_workers_per_gather = 3 processes. On the EC2 machines nproc was 2-4,
# so nproc was approximately the achievable degree of parallelism and the mismatch was
# small enough to be invisible. On an 8-core laptop it is 2.7x, and the signature describes
# capacity no query can reach. Measured on the M1 at several thread counts:
M1_COMPUTE_BY_THREADS = {1: 12.5315, 2: 24.6617, 3: 35.6222, 4: 45.6007, 8: 61.8163}
PG_EFFECTIVE_DOP = 3
APPROXIMATE = {"q16", "q20"}


def main():
    if not LAPTOP_CSV.exists():
        sys.exit(f"missing {LAPTOP_CSV} -- run scripts/collection/collect_laptop.py first")
    lap = pd.read_csv(LAPTOP_CSV)
    warm = lap[~lap["is_warmup"]]
    m1 = (warm.groupby("query_id")
          .agg(dev_time_p50=("time_ms", lambda x: float(np.percentile(x, 50))),
               dev_cv=("time_ms", lambda x: float(np.std(x, ddof=1) / np.mean(x))),
               n_reps=("time_ms", "size"),
               dop=("workers_launched", lambda x: 1 + float(np.median(x))))
          .reset_index())

    cells = load_cells()
    prod = cells[["machine_id", "query_id", "time_p50", "time_cv"]].rename(
        columns={"machine_id": "prod_machine", "time_p50": "prod_time_p50",
                 "time_cv": "prod_cv"})
    df = prod.merge(m1, on="query_id", how="inner")
    df["actual_scaling"] = df["prod_time_p50"] / df["dev_time_p50"]
    df["compute_ratio"] = df["prod_machine"].map(COMPUTE_MULTI) / M1_COMPUTE_MT
    df["cmp_only"] = 1.0 / df["compute_ratio"]
    df["naive"] = 1.0
    df["approximate_query"] = df["query_id"].isin(APPROXIMATE)
    df.to_csv(RESULTS_DIR / "tier1_laptop_dev.csv", index=False)

    y = df["actual_scaling"].to_numpy()
    overall = {k: score_all(y, df[k].to_numpy()) for k in ("cmp_only", "naive")}

    # Does matching the probe's thread count to the achievable DOP fix it?
    dop_sweep = {}
    for t, score in sorted(M1_COMPUTE_BY_THREADS.items()):
        pred = (score / df["prod_machine"].map(COMPUTE_MULTI)).to_numpy()
        dop_sweep[t] = {**score_all(y, pred), "m1_score": score,
                        "mean_predicted_scaling": float(pred.mean())}

    # In-hull reference: the same predictor on the EC2->EC2 pairs it was developed on.
    pairs, machines = build_pairs(cells, COMPUTE_MULTI)
    ec2 = score_all(pairs["scaling_factor_p50"].to_numpy(),
                    (1.0 / pairs["compute_ratio"]).to_numpy())

    per_machine = {m: score_all(df[df.prod_machine == m]["actual_scaling"].to_numpy(),
                                df[df.prod_machine == m]["cmp_only"].to_numpy())
                   for m in sorted(df["prod_machine"].unique())}

    L = ["# arm64 laptop as the dev machine (Tier 1, item 27)", "",
         "First test of the actual product claim: predict production runtime from a developer's",
         "laptop. Dev = Apple M1 (arm64, 8 cores, 8 GB, PostgreSQL 15.17); prod = the five EC2",
         "instances. 22 queries including q15, 21 repetitions with repetition 0 discarded as",
         "cold-cache warmup.", "",
         "## The bandwidth axis is unavailable, and that is a finding", "",
         "The STREAM-triad probe behind the EC2 bandwidth figures (8.44 - 14.9) **is not in the",
         "repository** — only `compute_probe.c` survives. It cannot be re-run, so the M1 cannot",
         "be placed on that axis at all. This blocks `naive-linear` (`1/bandwidth_ratio`) — the",
         "project's own headline baseline — from being evaluated laptop->cloud. The instrument",
         "behind the most-quoted number in the project is lost, in the same way the query files",
         "were. Only `cmp_only` can be tested, because the compute probe survives.", "",
         "## How far outside the training hull the M1 sits", "",
         "| axis | M1 | EC2 range | position |", "|---|---:|---|---|",
         f"| single-core GFLOPS | {M1_COMPUTE_ST:.2f} | 8.04 – 15.81 | inside |",
         f"| multi-core GFLOPS | {M1_COMPUTE_MT:.2f} | 24.97 – 40.81 | "
         f"**{M1_COMPUTE_MT / 40.8071:.2f}x above the maximum** |", "",
         "## Result", "",
         "| predictor | MAPE % | MdAPE % | q-error (median) |", "|---|---:|---:|---:|"]
    for k in ("cmp_only", "naive"):
        s = overall[k]
        L.append(f"| {k} (laptop→cloud) | {s['mape']:.2f} | {s['mdape']:.2f} | "
                 f"{s['qerror_median']:.3f} |")
    L.append(f"| *cmp_only on EC2→EC2, for reference* | *{ec2['mape']:.2f}* | "
             f"*{ec2['mdape']:.2f}* | *{ec2['qerror_median']:.3f}* |")
    L += ["", "Per production machine:", "",
          "| prod machine | MAPE % | MdAPE % |", "|---|---:|---:|"]
    for m, s in per_machine.items():
        L.append(f"| {m} | {s['mape']:.2f} | {s['mdape']:.2f} |")

    L += ["", "## The compute signature is a hardware x configuration artifact", "",
          "The 324% error is not simple extrapolation failure; most of it is a defect in how the "
          "signature is defined. `compute_probe` was run with **`nproc` threads**, but a "
          f"PostgreSQL query can only use **1 + max_parallel_workers_per_gather = "
          f"{PG_EFFECTIVE_DOP}** processes. On the EC2 machines `nproc` was 2-4, so `nproc` was "
          "roughly the achievable parallelism and the mismatch was invisible. On an 8-core "
          "laptop it is 2.7x, and the score describes capacity no query can ever reach.", "",
          "Re-deriving the M1 score at each thread count, changing nothing else:", "",
          "| probe threads | M1 GFLOPS | mean predicted scaling | MAPE % | MdAPE % |",
          "|---:|---:|---:|---:|---:|"]
    for t, v in dop_sweep.items():
        mark = "  ← DOP-matched" if t == PG_EFFECTIVE_DOP else ""
        L.append(f"| {t}{mark} | {v['m1_score']:.2f} | {v['mean_predicted_scaling']:.3f} | "
                 f"{v['mape']:.1f} | {v['mdape']:.1f} |")
    L += ["",
          f"Matching the thread count to the achievable DOP cuts the error from "
          f"**{dop_sweep[8]['mape']:.0f}%** to **{dop_sweep[PG_EFFECTIVE_DOP]['mape']:.0f}%** — "
          f"more than half the error was a measurement-definition bug rather than a modelling "
          f"failure. It is still unusable, but the mechanism is now identified.", "",
          f"**This impugns the headline result too.** The 21.61% figure rests on a multi-core "
          f"compute score measured at `nproc` threads, and `nproc` correlates with the score at "
          f"r = +0.841 (`tier1_hw_signature.md`). On a 5-machine set where `nproc` happened to "
          f"approximate the achievable DOP, that bias was absorbed. It is not a property of the "
          f"hardware, and it will not hold on machines with more cores than "
          f"`max_parallel_workers_per_gather` allows. **Any Tier 2 collection must probe at the "
          f"achievable DOP, not at `nproc`, and the five EC2 machines must be re-probed that "
          f"way.**", "",
          f"Note the actual mean scaling factor is {float(df['actual_scaling'].mean()):.3f}: the "
          f"EC2 instances are on average *faster* than the M1 on this workload, despite the M1 "
          f"scoring 1.5x higher on aggregate GFLOPS. c5a runs these queries ~1.7x faster than "
          f"the laptop. Whatever the compute signature measures, it is not what determines "
          f"TPC-H runtime across an ISA boundary.", ""]

    degradation = overall["cmp_only"]["mape"] - ec2["mape"]
    L += ["", "## Verdict", ""]
    if overall["cmp_only"]["mape"] > 2 * ec2["mape"]:
        L.append(f"**The method does not transfer from laptop to cloud.** Error rises from "
                 f"{ec2['mape']:.2f}% within the EC2 hull to {overall['cmp_only']['mape']:.2f}% "
                 f"laptop->cloud, a {overall['cmp_only']['mape'] / ec2['mape']:.1f}x "
                 f"degradation. A two-scalar hardware signature fitted on five cloud instances "
                 f"does not describe an arm64 laptop, and the product claim as currently stated "
                 f"is not supported.")
    elif degradation > 5:
        L.append(f"**Degrades substantially but does not collapse**: {ec2['mape']:.2f}% within "
                 f"the EC2 hull against {overall['cmp_only']['mape']:.2f}% laptop->cloud "
                 f"({degradation:+.2f}pp). Usable as a relative-regression signal, not as an "
                 f"absolute predictor.")
    else:
        L.append(f"**It transfers.** {overall['cmp_only']['mape']:.2f}% laptop->cloud against "
                 f"{ec2['mape']:.2f}% within the EC2 hull ({degradation:+.2f}pp), despite the "
                 f"M1 sitting {M1_COMPUTE_MT / 40.8071:.2f}x outside the training range on the "
                 f"only usable axis. That is a stronger result than anything in the EC2-only "
                 f"evaluation, because it is a genuine extrapolation to a different ISA.")
    L += ["",
          "Either way this is a single dev machine, so it establishes the direction and not a "
          "distribution. The `naive` row (assume production equals the laptop) is the thing any "
          "developer does implicitly today, and is the baseline that actually matters "
          "commercially.", "",
          "## Caveats", "",
          "- **One laptop.** n=1 on the dev side.",
          "- **No bandwidth axis** (above), so the headline baseline cannot be compared.",
          "- **q16 and q20 are APPROXIMATE reconstructions**; their laptop and EC2 rows describe "
          "slightly different queries. Flagged per row in the CSV.",
          "- **q15 has no EC2 counterpart** and is therefore excluded from the join.",
          "- The M1's 8 cores (4 performance + 4 efficiency) are heterogeneous, which the "
          "single scalar compute score cannot express — plausibly part of why a multi-core "
          "aggregate figure of 61.82 GFLOPS does not translate into proportionally faster "
          "query execution.", ""]
    (RESULTS_DIR / "tier1_laptop_dev.md").write_text("\n".join(L) + "\n")
    print(df.groupby("prod_machine")[["actual_scaling", "cmp_only"]].mean().to_string())
    print()
    for k in ("cmp_only", "naive"):
        print(f"  {k:<10} MAPE {overall[k]['mape']:6.2f}")
    print(f"  EC2->EC2 reference: {ec2['mape']:.2f}")
    print("\n  DOP sweep:")
    for t, v in dop_sweep.items():
        print(f"    {t} threads ({v['m1_score']:.2f} GFLOPS): MAPE {v['mape']:7.1f}")
    print(f"\nWrote {RESULTS_DIR / 'tier1_laptop_dev.md'}")


if __name__ == "__main__":
    main()
