# Tier 1 results

Items 17–29 of [TASKS.md](TASKS.md), executed 2026-10-12. Every number below is produced by a
script in `scripts/` and written to `results/`. Where an item could not be completed, it says so
and says why.

## The short version

Tier 1 was meant to make the method defensible. It did something more useful: it found that
**the method's central structure does not hold up**, and it found exactly why.

1. **The hardware signature is one-dimensional.** Bandwidth and multi-core compute correlate at
   **r = +0.958** across the five machines; PCA gives **97.9% / 2.1%**. There is one latent
   machine-quality axis measured twice, and it is largely **core count** (r = +0.841).
2. **Therefore the bottleneck taxonomy cannot matter, and it doesn't.** The classifier is
   decorative (uniform weights score *better*), the hand labels are not supported by measured
   elasticity, and measured elasticity does not help as a weighting either. A roofline needs two
   independent resources; there is one.
3. **`1/compute_ratio` alone is the best predictor** — 20.06% MAPE against 23.05% for the
   bandwidth baseline and 21.61% for the full roofline — but **not significantly so** at n=5.
4. **Plan structure genuinely helps on unseen queries**: 17.29% → **13.29%**. The shipped
   13-feature set is *worse* than two hardware ratios alone.
5. **The compute signature is a hardware × configuration artifact**, not a hardware property.
   It is probed at `nproc` threads while a query can only use `1 + max_parallel_workers_per_gather = 3`.
6. **Laptop → cloud does not work.** 324% MAPE, against 20% within the EC2 hull.
7. **Conformal prediction delivers the coverage the quantile models only claimed**, at ~29%
   wider intervals.

Two instruments behind published numbers were found to be **lost**: the 21 query files
(reconstructed, 19/21 confirmed exactly) and the **STREAM-triad bandwidth probe** (not
recoverable — see item 26).

---

## Item-by-item

| # | Item | Status | Where |
|---|---|---|---|
| 17 | Elasticity rig | **Done**, 3 axes, 338 measurements | `scripts/collection/elasticity_probe.py`, `results/elasticity_raw.csv` |
| 18 | Elasticity replaces hand labels | **Done — hypothesis refuted** | `results/tier1_elasticity.md` |
| 19 | Classifier ablation | **Done — classifier is decorative** | `results/tier1_ablation.md` |
| 20 | Conformal quantile regression | **Done — coverage delivered** | `results/tier1_conformal.md` |
| 21 | Weighted conformal (covariate shift) | **Done — no measurable gain at n=4 calibration machines** | `results/tier1_conformal.md` |
| 22 | Derive the confidence threshold | **Cannot be done at n=5** — 6 operating points total | `results/tier1_gate.md` |
| 23 | Fix or retire the three-state gate | **Done — retired as a 3-state gate** | `results/tier1_gate.md` |
| 24 | Log-space modelling | **Done — better points, worse intervals** | `results/tier1_conformal.md` |
| 25 | Plan-structure features | **Done — clear positive result** | `results/tier1_plan_features.md` |
| 26 | "Are two scalars enough?" | **Done — you don't have two** | `results/tier1_hw_signature.md` |
| 27 | arm64 laptop as dev | **Done — it fails** | `results/tier1_laptop_dev.md` |
| 28 | Fix Q15 | **Done** | `results/q15_resolution.md` |
| 29 | Propagate measurement noise | **Done — noise floor 0.60%, not the binding constraint** | `results/tier1_conformal.md` |

---

## 1. The structural finding (item 26)

| pair | Pearson r |
|---|---:|
| bandwidth vs multi-core compute | **+0.958** |
| multi-core compute vs vCPU count | **+0.841** |
| bandwidth vs single-core compute | −0.380 |

PCA on the standardised two-axis space: **axis 1 = 97.9%**, axis 2 = 2.1%.

The "two-scalar hardware signature" is one axis measured twice. This is not a detail — it
forces most of the project's other results:

- A roofline presumes two independent resources so that knowing which one binds is informative.
  At r = 0.958 there is no second resource. The roofline's +1.44pp is about what that should buy.
- Any weighting scheme is interpolating between two estimates of the same quantity, so the
  classifier, the hand labels and the measured elasticities must all be roughly equivalent —
  and they are (21.55–21.76%).
- The best predictor must be whichever single probe measures the latent axis most cleanly, not
  a blend. It is.

**"Are two scalars enough" is unanswerable here**, because the two have not been shown to be
two. Settling it needs machines chosen to *decorrelate* the axes: high-core/low-bandwidth and
the reverse, arm64 beside x86, NVMe beside EBS.

`scripts/collection/hw_probe_suite.c` (7 axes: integer, scalar FP, vector FP, vector FP MT,
branch cost, L1/L2/L3/DRAM latency, bandwidth 1T and MT) is written and validated — its
`fp_vector` figure of 12.314 GFLOPS matches `compute_probe.c`'s 12.53 on the same machine to
1.8%, and the latency hierarchy is strictly monotonic (1.29 → 4.81 → 7.89 → 112.05 ns). It has
run on one machine, because the EC2 instances are terminated. It is Tier 2 infrastructure.

## 2. The bottleneck taxonomy does not survive (items 18, 19)

Analytical formula under LOMO, classifier refit per fold:

| weighting | MAPE % | MdAPE % |
|---|---:|---:|
| learned classifier | 21.61 | 18.14 |
| hand labels, one-hot | 21.62 | 18.08 |
| **uniform 0.25** | **21.55** | **17.78** |
| measured elasticity | 21.76 | 18.08 |
| bandwidth only (= baseline) | 23.05 | 19.37 |
| **compute only** | **20.06** | **15.87** |
| per-row oracle (bound) | 17.07 | 13.10 |

A fixed uniform weighting that uses no query-specific information **beats** the learned
classifier. The measured elasticities beat nothing. Accuracy rises monotonically as weight moves
from bandwidth onto compute and peaks when all of it is on compute.

The measured elasticities also contradict the labels directly. Mean sensitivity by hand class:

| hand label | compute sensitivity | bandwidth sensitivity |
|---|---:|---:|
| bandwidth | 0.368 | 0.776 |
| compute | **0.368** | 0.395 |
| io | 0.172 | 0.579 |
| mixed | 0.363 | **0.861** |

`compute`- and `bandwidth`-labelled queries have *identical* measured compute sensitivity, and
`mixed` has the highest bandwidth sensitivity of any class. The diagonal does not hold. Four of
21 queries show a positive `e_dop` (slower with more cores). Two — q11 and q20 — show DOP = 1:
the planner never parallelises them, which is independently what `fix3` found, a useful check
that the rig measures what it claims.

## 3. `cmp_only` is best, and 5 machines cannot prove it (item 19)

| comparison | point | 95% CI | verdict |
|---|---:|---|---|
| naive-linear − analytical | +1.44pp | [0.32, 2.56] | **significant** |
| naive-linear − cmp_only | +2.99pp | [−0.09, 6.07] | not significant |
| analytical − cmp_only | +1.55pp | [−0.41, 3.51] | not significant |

The larger improvement is the less significant one. The roofline's small gain is *consistent*
across folds so the paired difference has low variance; `cmp_only` wins big on r5n and z1d and
loses on c5a and m5a, and at 5 clusters that spread swamps the mean. The honest statement:
**`cmp_only` has the best point estimate on every error metric, and n=5 cannot establish it.**

## 4. Plan structure works (item 25)

Leave-one-**query**-out is the honest test: under LOMO every query is in training and all six
existing dev-side features are machine-invariant, so the model simply memorises the query.

| feature set | n | LOQO (new query) | LOMO (new machine) |
|---|---:|---:|---:|
| hardware ratios only | 2 | 16.10 | 25.45 |
| **shipped 13 features** | 13 | **17.29** | 23.90 |
| 13 + plan structure | 53 | 13.86 | 23.33 |
| **plan structure + hardware ratios** | 42 | **13.29** | 24.77 |

Two things: plan structure cuts new-query error by **4.0pp (23% relative)**, and **the shipped
feature set is worse than using two hardware ratios and nothing else**. The root-node scalars
and bottleneck probabilities actively hurt on unseen queries. This is the direction worth
investing in, and the argument for a tree-structured encoder once Tier 2 supplies enough
queries to train one.

## 5. Calibrated intervals (items 20, 21, 24, 29)

One-sided upper bounds on `scaling_factor_p50`, leave-one-machine-out:

| method | nominal | empirical (mean) | worst machine | rel. width |
|---|---:|---:|---:|---:|
| GBR quantile (shipped) | 95% | **84.6%** ⚠ | 79.8% | 1.457 |
| GBR log-space | 95% | **72.9%** ⚠ | 61.9% | 1.243 |
| split conformal | 95% | **97.0%** ✓ | 90.5% | 1.871 |
| weighted conformal | 95% | **97.0%** ✓ | 90.5% | 1.890 |

The shipped quantile models under-cover badly. Conformal delivers the guarantee, at **~29%
wider** intervals — that is the real price of an honest p95. Two caveats worth more than the
headline: marginal coverage holds but **conditional coverage on the worst single machine still
fails** (90.5% at nominal 95%), which is the c5a problem reappearing as theory predicts; and
**weighting by hardware distance buys nothing**, because with 4 calibration machines there are
only 4 distinct weights.

Log-space fitting (item 24) gives better point estimates but worse intervals, because a global
residual quantile does not adapt. Use it for points, conformal for bounds.

Item 29: the measurement-noise floor on the target is **0.60%** relative standard error. Model
error is ~20%. **Noise is not the binding constraint** — the data is far more precise than the
model needs.

## 6. The gate (items 22, 23)

**Item 22 cannot be completed.** `hardware_distance_confidence` depends only on the production
machine's signature, so under LOMO it takes one value per machine — **6 operating points in
total**. Worse, abstaining does not monotonically reduce risk: 90% coverage gives 24.28% MAPE
but 80% gives 25.25% and 70% gives 26.17%. A selective predictor whose risk rises as it
abstains more is not working.

And the gate guards the wrong thing. Split at the shipped 0.6 threshold:

| predictor | MAPE, low confidence | MAPE, high confidence | ratio |
|---|---:|---:|---:|
| learned GBR | 38.15% | 22.31% | **1.71x** |
| analytical | 18.79% | 21.93% | 0.86x |
| cmp_only | 20.10% | 20.06% | 1.00x |

It works on the learned model, is **inverted** on the analytical formula, and is flat on
`cmp_only`. Since the project recommends deploying a formula, **the confidence gate protects the
one component it does not recommend shipping.** This also reconciles the old "~75% vs ~27%"
claim: right direction, inflated magnitude, and never a property of the system as a whole.

**Item 23**: a conformal gate was built and it works, but it reveals that the three-state design
is wrong. At τ = 1.5, PASS covers 17.4% of pairs with a **1.37% actual breach rate** (nominal
≤5% ✓), WARN covers 82.6%, and **BLOCK is empty at every τ tested**. With these interval widths
you can sometimes guarantee "safe" and never guarantee "unsafe". The shipped point-estimate gate
passes 87.7% of pairs with a 6.24% breach rate — it green-lights breaches above the acceptable
rate. **Retire the three-state gate; ship a two-state guaranteed-pass / defer gate.**

## 7. Laptop → cloud fails (item 27)

Apple M1 (arm64, 8 cores) as dev, the five EC2 instances as prod. 22 queries including q15, 21
repetitions with repetition 0 discarded.

| predictor | MAPE % | MdAPE % |
|---|---:|---:|
| cmp_only, laptop→cloud | **324.2** | 203.2 |
| naive (prod = laptop) | 123.4 | 57.4 |
| *cmp_only on EC2→EC2, reference* | *20.06* | *15.87* |

**A 16x degradation, and the trivial baseline beats the model.** The model predicts EC2 is
1.5–2.5x slower than the laptop; actual mean scaling is **0.858** — the EC2 instances are on
average *faster*, with c5a ~1.7x faster.

Most of that is a definition bug, not extrapolation. `compute_probe` is run at `nproc` threads,
but a query can use only `1 + max_parallel_workers_per_gather = 3`. Re-deriving the M1 score at
each thread count, changing nothing else:

| probe threads | M1 GFLOPS | MAPE % |
|---:|---:|---:|
| 1 | 12.53 | 61.4 |
| 2 | 24.66 | 95.6 |
| **3 (DOP-matched)** | 35.62 | **153.2** |
| 4 | 45.60 | 217.0 |
| 8 (`nproc`, as collected) | 61.82 | 324.2 |

DOP-matching halves the error. (The monotonic trend is partly MAPE's asymmetry — it bounds
under-prediction at 100% but not over-prediction — so read the direction, not the ranking.)

**This impugns the headline result too.** The 21.61% figure rests on a score probed at `nproc`,
and `nproc` correlates with that score at r = +0.841. On five machines where `nproc` happened to
approximate the achievable DOP, the bias was absorbed. It is not a hardware property. **Tier 2
must probe at the achievable DOP, and the five EC2 machines must be relaunched and re-probed.**

Also recorded: the M1's 8 cores are 4 performance + 4 efficiency, which one scalar cannot
express.

## 8. Lost instruments

The 21 query files were outside version control and the directory is gone. They are
reconstructed in `scripts/collection/queries/` and validated against
`results/plan_structure_features.csv`, which fingerprints the originals: **plan shape exact for
21/21, output cardinality exact for 19/21**. q11 uses `KENYA` (not the textbook `GERMANY`) and
q18 uses `> 314` (not 300) — both recovered by search and confirmed on all five fingerprint
fields. q16 and q20 remain **approximate** (+0.95% and −3.63% on output rows) and are flagged in
every derived artifact.

**The STREAM-triad bandwidth probe is not in the repository and is not recoverable.** Half the
hardware signature, and the basis of the `naive-linear` baseline that every result is compared
against, has no reproducible instrument. No new machine can be placed on that axis. This is why
item 27 could not evaluate the project's own baseline laptop→cloud.

## What Tier 1 implies for Tier 2

- Choose machines to **decorrelate** bandwidth from core count. This is now the single most
  important design constraint on the expansion, and it was not previously identified.
- **Re-probe hardware at the achievable DOP**, with `hw_probe_suite.c`, including relaunching
  the five original instances so the existing data stays comparable.
- **Invest in plan-structure features**, not in the bottleneck classifier. The classifier can be
  deleted; a tree encoder is the thing worth building.
- Keep conformal bounds and the two-state gate. Drop the three-state gate and, unless a learned
  model is shipped, the confidence gate.
- Report `cmp_only` as the leading predictor with its CI stated honestly, not the roofline.
