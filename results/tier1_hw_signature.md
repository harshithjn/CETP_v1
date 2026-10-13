# Is a two-scalar hardware signature enough? (Tier 1, item 26)

## The two axes are not independent

Across the five machines, in the signature the headline result actually uses:

| pair | Pearson r |
|---|---:|
| bandwidth vs **multi-core** compute | **+0.958** |
| bandwidth vs single-core compute | -0.380 |
| single-core vs multi-core compute | -0.525 |

Principal components of the standardised (bandwidth, multi-core compute) space:

- axis 1: **97.9%** of the variance
- axis 2: **2.1%** of the variance

**The hardware signature is effectively one-dimensional.** It is not two scalars in any useful sense — it is one latent machine-quality axis measured twice, at r = 0.958.

## What that explains

This is not a curiosity; it accounts for most of what the project has observed.

1. **Why the roofline formula gains so little.** A roofline presumes bandwidth and compute are *independent* resources, so that knowing which one binds tells you something. At r = 0.958 there is no second resource to be bound by, and reweighting between the two terms is interpolating between two estimates of the same quantity. A +1.44pp gain is about what that should buy.
2. **Why the bottleneck classifier is decorative** (`tier1_ablation.md`) and why **measured elasticities do not help as weights either** (`tier1_elasticity.md`). Both findings are forced: there is nothing for a weighting to select between.
3. **Why `1/compute_ratio` alone is the best predictor.** If there is one latent axis, the best predictor is whichever single probe measures it most cleanly — not a blend.
4. **What that axis largely is: core count.** r(multi-core compute, vCPU) = **+0.841**, and r(bandwidth, vCPU) = +0.705. The two 4-vCPU machines (c5a, m5a) sit at the top of both axes. Consistent with the causally confirmed parallelism mechanism (r = 0.458, p = 0.037): PostgreSQL goes parallel, so aggregate capacity is what matters, and aggregate capacity tracks core count.

## So, are two scalars enough?

**The question cannot be answered on this dataset, and not for want of modelling.** Five machines give five points; two axes that are 96% correlated give one effective dimension. There is no room to detect whether a third or fourth axis would help, because the existing two have not been shown to be two.

Settling it requires machines that **decorrelate** the axes — deliberately chosen so high bandwidth does not imply many cores. Concretely: high-core/low-bandwidth and low-core/high-bandwidth instances, arm64 alongside x86, and instance-local NVMe alongside EBS. That is the Tier 2 expansion, and this is the argument for choosing those machines deliberately rather than sampling instance types at random.

## The probe suite is built and validated

`scripts/collection/hw_probe_suite.c` measures seven axes instead of two: integer throughput, scalar FP (latency-bound), vector FP, vector FP multi-threaded, unpredictable-branch cost, the L1/L2/L3/DRAM latency hierarchy, and single- and multi-threaded STREAM bandwidth.

It has been run on one machine only — the Apple M1 — because the five EC2 instances are terminated:

| probe | M1 |
|---|---:|
| int_scalar_gops | 3.793 |
| fp_scalar_gflops | 1.567 |
| fp_vector_gflops | 12.314 |
| fp_vector_gflops_mt | 58.467 |
| branch_cost_ns | 0.1573 |
| cache_l1_ns | 1.293 |
| cache_l2_ns | 4.812 |
| cache_l3_ns | 7.894 |
| mem_latency_ns | 112.051 |
| mem_bandwidth_gbs_1t | 55.38 |
| mem_bandwidth_gbs_mt | 47.817 |

Two validation checks pass. `fp_vector_gflops` = 12.314 against `compute_probe.c`'s 12.53 on the same machine (1.8% apart) — two independently written probes agreeing that closely is good evidence both measure what they claim. And the latency hierarchy is strictly monotonic (1.29 → 4.81 → 7.89 → 112.05 ns), which a broken pointer-chase would not produce.

**This is infrastructure, not a result.** Its value is realised when it runs across the Tier 2 machine set; until then it establishes only that the measurement is ready and sane.

## A prerequisite that must be fixed first

The STREAM-triad probe that produced the EC2 bandwidth figures **is not in the repository**. Only `compute_probe.c` survives. So the bandwidth axis — half of the current signature, and the basis of the `naive-linear` baseline every result is compared against — has no reproducible instrument, and no new machine can be placed on it. `hw_probe_suite.c` supersedes it, but on its own scale: numbers it produces are not comparable to the existing 8.44–14.9 figures. Either the whole signature is re-measured with the new suite, or the old bandwidth numbers stay unreproducible forever. Re-measuring is the only honest option, and it means the five EC2 machines must be relaunched and re-probed as part of Tier 2.

