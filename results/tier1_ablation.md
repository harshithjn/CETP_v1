# Classifier ablation (Tier 1, item 19)

Does the bottleneck classifier contribute anything to the analytical roofline
formula, or would any weighting of its three terms do as well?

MAPE % on `scaling_factor_p50`, multi-core compute signal, classifier refit inside
every fold. `bw_only` is identical to the naive-linear baseline by construction.
`oracle` picks the best of the three terms per row with hindsight, so it bounds what
*any* weighting scheme could achieve.

| weighting | LOMO | LOQO | LOMQO |
|---|---:|---:|---:|
| learned | 21.61 | 21.66 | 21.64 |
| onehot | 21.62 | 21.62 | 21.62 |
| uniform | 21.55 | 21.55 | 21.55 |
| bw_only | 23.05 | 23.05 | 23.05 |
| cmp_only | 20.06 | 20.06 | 20.06 |
| oracle | 17.07 | 17.07 | 17.07 |

## Verdict

Under LOMO the learned classifier scores **21.61%** against **21.55%** for a fixed uniform weighting that uses no query-specific information at all — a difference of **-0.06pp**.

**The classifier is decorative.** A constant weighting matches it to within half a percentage point, so essentially all of the analytical formula's accuracy comes from the hardware ratios, not from knowing which resource a query is bound by. The component whose ground truth is 21 hand-assigned judgement calls is contributing nothing measurable.

The hand labels used as a hard one-hot score **21.62%**, so even perfect knowledge of the (hand-assigned) class is worth +1.43pp against the pure bandwidth ratio.

The per-row oracle bound is **17.07%**. That is the ceiling for any scheme that weights these three terms, so it also bounds how much a better classifier — or the measured elasticity vectors from item 17/18 — could possibly gain within this functional form. Beating it requires a different formula, not better weights.

## The result that matters: `1/compute_ratio` alone is the best predictor

`cmp_only` — a single number, the ratio of multi-core aggregate GFLOPS, with no classifier, no bottleneck weighting and no second hardware axis — scores **20.06% MAPE**, and it is also the best on MdAPE (15.87%) and on median q-error. It beats the full analytical roofline (21.61%) and the bandwidth baseline (23.05%).

**But the improvement is not statistically significant, and the weaker one is.** Paired cluster bootstrap over the 5 held-out machines:

| comparison | point | 95% CI | verdict |
|---|---:|---|---|
| naive-linear − analytical | +1.44pp | [0.32, 2.56] | **SIGNIFICANT** |
| naive-linear − cmp_only | +2.99pp | [-0.09, 6.07] | not significant |
| analytical − cmp_only | +1.55pp | [-0.41, 3.51] | not significant |
| gated − cmp_only | +2.25pp | [-0.39, 4.89] | not significant |

This is not a contradiction, it is the whole lesson of a 5-machine evaluation. The analytical formula's small gain is *consistent* — it improves on the bandwidth ratio on nearly every fold, so the paired difference has low variance and the CI clears zero. `cmp_only`'s larger gain is *inconsistent*:

| held-out machine | naive-linear | analytical | cmp_only |
|---|---:|---:|---:|
| c5a | 19.68 | 19.80 | **20.42** |
| c7i | 27.75 | 25.39 | **21.83** |
| m5a | 20.58 | 20.36 | **21.67** |
| r5n | 25.97 | 22.64 | **18.32** |
| z1d | 21.26 | 19.87 | **18.08** |

It wins big on r5n and z1d and loses on c5a and m5a. With 5 clusters that spread swamps the mean. **The honest statement is: `cmp_only` has the best point estimate on every error metric, and 5 machines are not enough to establish it.** Settling this is precisely what the Tier 2 dataset expansion is for.

## Why this undermines the bottleneck taxonomy

| bottleneck class | naive-linear | analytical | cmp_only |
|---|---:|---:|---:|
| bandwidth | 17.64 | 17.61 | **14.10** |
| compute | 22.63 | 17.55 | **17.50** |
| io | 28.79 | 28.76 | **26.14** |
| mixed | 23.04 | 21.58 | **20.79** |

`cmp_only` is the best predictor on **every** class, including the ones labelled `bandwidth` (14.10% vs 17.61%) and `io` (26.14% vs 28.76%). A query hand-labelled bandwidth-bound is predicted better by the compute ratio than by the bandwidth ratio. That is hard to reconcile with the labels describing a real property of the queries, and it is consistent with the classifier ablation above: the taxonomy is not carrying information.

One guard against over-reading this: the same `1/compute_ratio` predictor on the **single-core** compute signal scores **51.44%**, far worse than everything. So the finding is not that compute ratios are magic — it is that the *multi-core aggregate* measurement captures what differentiates these machines, which is the parallelism mechanism already confirmed causally (r = 0.458, p = 0.037).

