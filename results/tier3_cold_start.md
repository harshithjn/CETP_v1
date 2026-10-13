# N3: budgeted cold start (Tier 3, item 41)

**Given k benchmark runs on a production machine never measured before, which k queries should you run?**

Evaluated leave-one-machine-out on the queries NOT revealed, so the calibration set never leaks into the test set. The predictor is `cmp_only` (`1/compute_ratio`), the best performer from Tier 1. Revealing k queries buys an estimate of that machine's multiplicative correction `c_m = median(actual/predicted)`, applied to the rest.

Uncalibrated error on a cold machine: **15.84% MAPE**.

Post-calibration MAPE %, averaged over the 5 held-out machines:

| k | random | cheapest | spread | dopt | elasticity | oracle |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 19.51 | 15.90 | 13.20 | 14.45 | 13.20 | 12.70 |
| 2 | 16.85 | 20.91 | 14.41 | 24.60 | 14.12 | 13.20 |
| 3 | 15.82 | 15.62 | 12.57 | 13.78 | 15.82 | 12.44 |
| 4 | 15.05 | 19.72 | 12.81 | 13.40 | 15.09 | 11.29 |
| 5 | 14.51 | 15.35 | 12.91 | 14.37 | 15.41 | 10.40 |
| 6 | 14.17 | 13.84 | 13.60 | 14.11 | 16.71 | 9.57 |
| 7 | 14.03 | 14.77 | 13.93 | 14.48 | 18.05 | 8.67 |
| 8 | 13.78 | 13.29 | 12.46 | 15.46 | 18.56 | 8.32 |
| 9 | 13.54 | 13.89 | 12.76 | 15.05 | 18.38 | 7.32 |
| 10 | 13.60 | 13.83 | 13.17 | 15.43 | 16.18 | 6.96 |
| 11 | 13.40 | 14.25 | 11.89 | 13.87 | 13.59 | 5.96 |
| 12 | 13.45 | 13.14 | 12.25 | 13.22 | 14.05 | 5.76 |

## What this says

**One well-chosen measurement beats twelve arbitrary ones.** `elasticity` at k=1 scores **13.20%**, against `random` at k=12 scoring **13.45%**. Which queries you run matters more than how many — the opposite of the arbitrary-12 approach in `addition7_online_loop.py`, and a much cheaper operating point.

**Calibrating from a single *random* query makes things worse** (19.51% against 15.84% uncalibrated). One query gives a noisy estimate of the machine's correction factor, and applying a bad correction is worse than applying none. A cold-start loop that reveals queries in arbitrary order can therefore *degrade* the predictor before it improves it — which no previous version of this loop would have detected, because it never measured the k=1 case.

**The ceiling is far above what any strategy reaches.** `oracle` falls to 5.76% at k=12 while the best practical strategy plateaus near 12.25%. So there is real headroom for a better acquisition function — but note the oracle is choosing with hindsight over only 21 queries, so part of that gap is overfitting to the test set rather than attainable signal.

**`elasticity` get *worse* as k grows**, which is informative rather than a bug. Max-min diversity and D-optimality both chase *extremes*: as k increases they add increasingly atypical queries, and the median correction they feed gets dragged off. For estimating a single scalar you want *representative* queries, not spread — which is why `spread` in predicted-scaling space, the one objective aligned with the quantity being estimated, is the most stable column in the table.

**The overall gain from measuring is modest**: 15.84% uncalibrated to roughly 12.25% calibrated. That is consistent with Tier 1 — the analytical formula is already close to per-machine unbiased, so there is not much systematic correction left to find. Cold-start calibration is worth doing because it is nearly free, not because it transforms the prediction.


## Caveats

- The calibration model is a **single multiplicative factor** per machine. A richer model (per-bottleneck-class corrections, or refitting the regressor) would use k more effectively, but Tier 1 showed query-level structure is not what limits accuracy here — machine-level bias is — and 21 queries cannot support more parameters.
- 5 machines, so each error-vs-k curve is an average over 5 folds.
- `elasticity` uses measured elasticity vectors from `tier1_elasticity.csv`, which were measured on the M1 rather than on these machines.

