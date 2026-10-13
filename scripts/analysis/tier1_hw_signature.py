"""
Is a two-scalar hardware signature enough? (TASKS.md Tier 1, item 26)

The project represents each machine by two numbers: STREAM-triad bandwidth and
multi-core compute throughput. Whether two axes are enough has never been tested -- it
is assumed, and the limitations section names a richer probe suite as probably mattering
more than further modelling.

This answers a prior question that turns out to settle the matter: are the two existing
axes independent at all?

scripts/collection/hw_probe_suite.c is the richer suite (7 axes: integer, scalar FP,
vector FP, vector FP multi-threaded, branch cost, the full cache/DRAM latency hierarchy,
and single- and multi-threaded bandwidth). It is written, compiled and validated, but it
CANNOT be run on the five EC2 instances, which are terminated. So it is infrastructure
for Tier 2 rather than a result today.

Writes results/tier1_hw_signature.md.
"""

import json
from pathlib import Path

import numpy as np

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"

BANDWIDTH = {"c5a": 14.9, "z1d": 9.77, "r5n": 9.39, "m5a": 10.01, "c7i": 8.44}
COMPUTE_MT = {"c5a": 40.8071, "z1d": 28.0804, "r5n": 24.9681, "m5a": 31.5366, "c7i": 25.8909}
COMPUTE_ST = {"c5a": 10.2585, "z1d": 15.8112, "r5n": 12.6870, "m5a": 8.0354, "c7i": 12.9979}
VCPU = {"c5a": 4, "m5a": 4, "r5n": 2, "z1d": 2, "c7i": 2}

# hw_probe_suite output on the Apple M1 (3 s per probe, 8 threads). Recorded here because
# it is the only machine the suite has been run on; EC2 instances are gone.
M1_PROBE = {
    "int_scalar_gops": 3.793, "fp_scalar_gflops": 1.567, "fp_vector_gflops": 12.314,
    "fp_vector_gflops_mt": 58.467, "branch_cost_ns": 0.1573, "cache_l1_ns": 1.293,
    "cache_l2_ns": 4.812, "cache_l3_ns": 7.894, "mem_latency_ns": 112.051,
    "mem_bandwidth_gbs_1t": 55.380, "mem_bandwidth_gbs_mt": 47.817,
}


def main():
    ks = sorted(BANDWIDTH)
    b = np.array([BANDWIDTH[k] for k in ks])
    mt = np.array([COMPUTE_MT[k] for k in ks])
    st = np.array([COMPUTE_ST[k] for k in ks])
    nc = np.array([VCPU[k] for k in ks], float)

    r_b_mt = float(np.corrcoef(b, mt)[0, 1])
    r_b_st = float(np.corrcoef(b, st)[0, 1])
    r_st_mt = float(np.corrcoef(st, mt)[0, 1])
    r_mt_nc = float(np.corrcoef(mt, nc)[0, 1])
    r_b_nc = float(np.corrcoef(b, nc)[0, 1])

    X = np.c_[(b - b.mean()) / b.std(), (mt - mt.mean()) / mt.std()]
    ev = np.linalg.svd(X, compute_uv=False) ** 2
    ev = ev / ev.sum()

    L = ["# Is a two-scalar hardware signature enough? (Tier 1, item 26)", "",
         "## The two axes are not independent", "",
         "Across the five machines, in the signature the headline result actually uses:", "",
         "| pair | Pearson r |", "|---|---:|",
         f"| bandwidth vs **multi-core** compute | **{r_b_mt:+.3f}** |",
         f"| bandwidth vs single-core compute | {r_b_st:+.3f} |",
         f"| single-core vs multi-core compute | {r_st_mt:+.3f} |", "",
         "Principal components of the standardised (bandwidth, multi-core compute) space:", "",
         f"- axis 1: **{ev[0]:.1%}** of the variance",
         f"- axis 2: **{ev[1]:.1%}** of the variance", "",
         "**The hardware signature is effectively one-dimensional.** It is not two scalars in "
         "any useful sense — it is one latent machine-quality axis measured twice, at "
         f"r = {r_b_mt:.3f}.", "",
         "## What that explains", "",
         "This is not a curiosity; it accounts for most of what the project has observed.", "",
         "1. **Why the roofline formula gains so little.** A roofline presumes bandwidth and "
         "compute are *independent* resources, so that knowing which one binds tells you "
         f"something. At r = {r_b_mt:.3f} there is no second resource to be bound by, and "
         "reweighting between the two terms is interpolating between two estimates of the same "
         "quantity. A +1.44pp gain is about what that should buy.",
         "2. **Why the bottleneck classifier is decorative** (`tier1_ablation.md`) and why "
         "**measured elasticities do not help as weights either** "
         "(`tier1_elasticity.md`). Both findings are forced: there is nothing for a weighting to "
         "select between.",
         "3. **Why `1/compute_ratio` alone is the best predictor.** If there is one latent axis, "
         "the best predictor is whichever single probe measures it most cleanly — not a blend.",
         f"4. **What that axis largely is: core count.** "
         f"r(multi-core compute, vCPU) = **{r_mt_nc:+.3f}**, and r(bandwidth, vCPU) = "
         f"{r_b_nc:+.3f}. The two 4-vCPU machines (c5a, m5a) sit at the top of both axes. "
         f"Consistent with the causally confirmed parallelism mechanism (r = 0.458, p = 0.037): "
         f"PostgreSQL goes parallel, so aggregate capacity is what matters, and aggregate "
         f"capacity tracks core count.", "",
         "## So, are two scalars enough?", "",
         "**The question cannot be answered on this dataset, and not for want of modelling.** "
         "Five machines give five points; two axes that are 96% correlated give one effective "
         "dimension. There is no room to detect whether a third or fourth axis would help, "
         "because the existing two have not been shown to be two.", "",
         "Settling it requires machines that **decorrelate** the axes — deliberately chosen so "
         "high bandwidth does not imply many cores. Concretely: high-core/low-bandwidth and "
         "low-core/high-bandwidth instances, arm64 alongside x86, and instance-local NVMe "
         "alongside EBS. That is the Tier 2 expansion, and this is the argument for choosing "
         "those machines deliberately rather than sampling instance types at random.", "",
         "## The probe suite is built and validated", "",
         "`scripts/collection/hw_probe_suite.c` measures seven axes instead of two: integer "
         "throughput, scalar FP (latency-bound), vector FP, vector FP multi-threaded, "
         "unpredictable-branch cost, the L1/L2/L3/DRAM latency hierarchy, and single- and "
         "multi-threaded STREAM bandwidth.", "",
         "It has been run on one machine only — the Apple M1 — because the five EC2 instances "
         "are terminated:", "",
         "| probe | M1 |", "|---|---:|"]
    for k, v in M1_PROBE.items():
        L.append(f"| {k} | {v} |")
    L += ["",
          "Two validation checks pass. `fp_vector_gflops` = 12.314 against `compute_probe.c`'s "
          "12.53 on the same machine (1.8% apart) — two independently written probes agreeing "
          "that closely is good evidence both measure what they claim. And the latency hierarchy "
          "is strictly monotonic (1.29 → 4.81 → 7.89 → 112.05 ns), which a broken pointer-chase "
          "would not produce.", "",
          "**This is infrastructure, not a result.** Its value is realised when it runs across "
          "the Tier 2 machine set; until then it establishes only that the measurement is ready "
          "and sane.", "",
          "## A prerequisite that must be fixed first", "",
          "The STREAM-triad probe that produced the EC2 bandwidth figures **is not in the "
          "repository**. Only `compute_probe.c` survives. So the bandwidth axis — half of the "
          "current signature, and the basis of the `naive-linear` baseline every result is "
          "compared against — has no reproducible instrument, and no new machine can be placed "
          "on it. `hw_probe_suite.c` supersedes it, but on its own scale: numbers it produces "
          "are not comparable to the existing 8.44–14.9 figures. Either the whole signature is "
          "re-measured with the new suite, or the old bandwidth numbers stay unreproducible "
          "forever. Re-measuring is the only honest option, and it means the five EC2 machines "
          "must be relaunched and re-probed as part of Tier 2.", ""]
    (RESULTS_DIR / "tier1_hw_signature.md").write_text("\n".join(L) + "\n")
    print(f"corr(bandwidth, compute_mt) = {r_b_mt:+.3f}")
    print(f"PCA: axis1 {ev[0]:.1%}, axis2 {ev[1]:.1%}  -> effectively 1-D")
    print(f"corr(compute_mt, vCPU) = {r_mt_nc:+.3f}")
    print(f"\nWrote {RESULTS_DIR / 'tier1_hw_signature.md'}")


if __name__ == "__main__":
    main()
