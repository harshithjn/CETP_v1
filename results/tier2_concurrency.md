# Concurrency sensitivity (Tier 2, item 35)

Every measurement in this project before now was single-query-at-a-time. Production is not. This measures runtime under N simultaneous clients of the same query, N in {1,2,4,8}, on the Apple M1 (8 cores).

`e_concurrency` is the log-log slope of runtime against client count. 0 means perfect scaling (the machine absorbs the extra load for free); **1 means fully serialised** (8 clients take 8x as long each, so throughput is flat).

## Aggregate

| clients | total throughput (queries/s) | median query slowdown vs 1 client |
|---:|---:|---:|
| 1 | 74.04 | 1.00x |
| 2 | 87.06 | 2.02x |
| 4 | 126.99 | 2.82x |
| 8 | 153.01 | 4.69x |

Median concurrency elasticity across queries: **+0.752** (range +0.286 to +0.984).

**Partial scaling.** Throughput rises with clients but sub-linearly, so contention is real but not total. This is the awkward middle for a predictor: the slowdown depends on the query, so a single global contention factor will not do.

## Per query

| query | e_concurrency | t at 1 client (ms) | slowdown at 8 clients | distinct plans |
|---|---:|---:|---:|---:|
| q11 | +0.984 | 130 | 8.57x | 1 |
| q15 | +0.931 | 743 | 6.53x | 1 |
| q4 | +0.895 | 185 | 4.91x | 1 |
| q1 | +0.877 | 2247 | 6.25x | 1 |
| q12 | +0.863 | 582 | 6.09x | 1 |
| q6 | +0.863 | 404 | 5.89x | 1 |
| q3 | +0.856 | 370 | 5.12x | 1 |
| q9 | +0.835 | 1719 | 5.78x | 1 |
| q13 | +0.793 | 1346 | 5.90x | 1 |
| q5 | +0.768 | 478 | 5.05x | 1 |
| q10 | +0.763 | 364 | 5.20x | 1 |
| q14 | +0.742 | 374 | 6.09x | 1 |
| q2 | +0.695 | 599 | 4.27x | 1 |
| q16 | +0.635 | 367 | 3.84x | 1 |
| q21 | +0.628 | 1472 | 3.68x | 1 |
| q18 | +0.581 | 3048 | 3.18x | 1 |
| q22 | +0.546 | 57 | 3.57x | 1 |
| q19 | +0.482 | 64 | 2.94x | 1 |
| q17 | +0.435 | 1056 | 2.52x | 1 |
| q8 | +0.327 | 335 | 2.00x | 1 |
| q7 | +0.293 | 2611 | 1.83x | 1 |
| q20 | +0.286 | 1406 | 1.88x | 1 |

## Sanity check: does the plan change with concurrency?

**0 of 22 queries** show more than one plan signature across concurrency levels. Concurrency is not a planner input, so the expected answer is zero.

## Limitations

- **One machine, and self-contention only** — N clients all run the *same* query. A real mixed workload contends differently, and that is not measured here.
- 8 clients on 8 cores with `max_parallel_workers_per_gather=2` means the requested parallelism alone exceeds the core count, so some of the slowdown is scheduler queueing rather than resource contention.
- 3 measured repetitions per cell, warmup discarded.

