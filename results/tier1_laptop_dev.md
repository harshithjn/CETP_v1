# arm64 laptop as the dev machine (Tier 1, item 27)

First test of the actual product claim: predict production runtime from a developer's
laptop. Dev = Apple M1 (arm64, 8 cores, 8 GB, PostgreSQL 15.17); prod = the five EC2
instances. 22 queries including q15, 21 repetitions with repetition 0 discarded as
cold-cache warmup.

## The bandwidth axis is unavailable, and that is a finding

The STREAM-triad probe behind the EC2 bandwidth figures (8.44 - 14.9) **is not in the
repository** — only `compute_probe.c` survives. It cannot be re-run, so the M1 cannot
be placed on that axis at all. This blocks `naive-linear` (`1/bandwidth_ratio`) — the
project's own headline baseline — from being evaluated laptop->cloud. The instrument
behind the most-quoted number in the project is lost, in the same way the query files
were. Only `cmp_only` can be tested, because the compute probe survives.

## How far outside the training hull the M1 sits

| axis | M1 | EC2 range | position |
|---|---:|---|---|
| single-core GFLOPS | 12.53 | 8.04 – 15.81 | inside |
| multi-core GFLOPS | 61.82 | 24.97 – 40.81 | **1.51x above the maximum** |

## Result

| predictor | MAPE % | MdAPE % | q-error (median) |
|---|---:|---:|---:|
| cmp_only (laptop→cloud) | 324.24 | 203.23 | 3.032 |
| naive (laptop→cloud) | 123.36 | 57.44 | 1.636 |
| *cmp_only on EC2→EC2, for reference* | *20.06* | *15.87* | *1.170* |

Per production machine:

| prod machine | MAPE % | MdAPE % |
|---|---:|---:|
| c5a | 306.21 | 203.23 |
| c7i | 376.48 | 270.01 |
| m5a | 252.57 | 163.44 |
| r5n | 318.29 | 203.00 |
| z1d | 367.65 | 224.02 |

## The compute signature is a hardware x configuration artifact

The 324% error is not simple extrapolation failure; most of it is a defect in how the signature is defined. `compute_probe` was run with **`nproc` threads**, but a PostgreSQL query can only use **1 + max_parallel_workers_per_gather = 3** processes. On the EC2 machines `nproc` was 2-4, so `nproc` was roughly the achievable parallelism and the mismatch was invisible. On an 8-core laptop it is 2.7x, and the score describes capacity no query can ever reach.

Re-deriving the M1 score at each thread count, changing nothing else:

| probe threads | M1 GFLOPS | mean predicted scaling | MAPE % | MdAPE % |
|---:|---:|---:|---:|---:|
| 1 | 12.53 | 0.427 | 61.4 | 51.5 |
| 2 | 24.66 | 0.841 | 95.6 | 43.5 |
| 3  ← DOP-matched | 35.62 | 1.215 | 153.2 | 74.7 |
| 4 | 45.60 | 1.555 | 217.0 | 123.7 |
| 8 | 61.82 | 2.108 | 324.2 | 203.2 |

Matching the thread count to the achievable DOP cuts the error from **324%** to **153%** — more than half the error was a measurement-definition bug rather than a modelling failure. It is still unusable, but the mechanism is now identified.

**This impugns the headline result too.** The 21.61% figure rests on a multi-core compute score measured at `nproc` threads, and `nproc` correlates with the score at r = +0.841 (`tier1_hw_signature.md`). On a 5-machine set where `nproc` happened to approximate the achievable DOP, that bias was absorbed. It is not a property of the hardware, and it will not hold on machines with more cores than `max_parallel_workers_per_gather` allows. **Any Tier 2 collection must probe at the achievable DOP, not at `nproc`, and the five EC2 machines must be re-probed that way.**

Note the actual mean scaling factor is 0.858: the EC2 instances are on average *faster* than the M1 on this workload, despite the M1 scoring 1.5x higher on aggregate GFLOPS. c5a runs these queries ~1.7x faster than the laptop. Whatever the compute signature measures, it is not what determines TPC-H runtime across an ISA boundary.


## Verdict

**The method does not transfer from laptop to cloud.** Error rises from 20.06% within the EC2 hull to 324.24% laptop->cloud, a 16.2x degradation. A two-scalar hardware signature fitted on five cloud instances does not describe an arm64 laptop, and the product claim as currently stated is not supported.

Either way this is a single dev machine, so it establishes the direction and not a distribution. The `naive` row (assume production equals the laptop) is the thing any developer does implicitly today, and is the baseline that actually matters commercially.

## Caveats

- **One laptop.** n=1 on the dev side.
- **No bandwidth axis** (above), so the headline baseline cannot be compared.
- **q16 and q20 are APPROXIMATE reconstructions**; their laptop and EC2 rows describe slightly different queries. Flagged per row in the CSV.
- **q15 has no EC2 counterpart** and is therefore excluded from the join.
- The M1's 8 cores (4 performance + 4 efficiency) are heterogeneous, which the single scalar compute score cannot express — plausibly part of why a multi-core aggregate figure of 61.82 GFLOPS does not translate into proportionally faster query execution.

