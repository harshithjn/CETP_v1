# Pre-registered analysis plan

Tier 5, final item. Written **before** the machine-diversity expansion, so the conclusion
cannot be chosen after seeing the data.

## The question

Can a production machine's query runtime be predicted from development-machine profiling,
well enough and reliably enough to gate a deployment — and what does a "hardware signature"
actually have to capture for that to work?

## What is already established (and will not be re-litigated)

On 5 EC2 instance types × 21 TPC-H queries at SF1, leave-one-machine-out:

- analytical roofline **21.61%** MAPE vs bandwidth-ratio baseline **23.05%**, improvement
  **+1.44pp**, 95% cluster-bootstrap CI **[0.32, 2.56]** — significant.
- `1/compute_ratio` alone scores **20.06%**, the best point estimate on every metric, but its
  CI **[-0.09, 6.07]** includes zero.
- The two signature axes correlate at **r = +0.958** (97.9% of variance on one component).

## The pre-registered commitment

**Primary hypothesis H1.** On ≥30 capability configurations with *decorrelated* axes
(container CPU × memory grid plus free real machines), a hardware signature with more than
one effective dimension will beat a one-dimensional one by a margin whose 95%
cluster-bootstrap CI excludes zero.

**If H1 is rejected, that is the paper.** The claim becomes: *a single capability scalar is
sufficient for cross-hardware query runtime prediction, and the additional axes every system
in this space encodes are redundant.* That result is more useful than a marginal positive,
and it is pre-committed here so it cannot be reframed as a failure later.

**Secondary hypotheses, each with its outcome pre-committed:**

| # | Hypothesis | If rejected |
|---|---|---|
| H2 | Plan-structure features beat root-node scalars on unseen queries | Report that plan structure is redundant given hardware ratios — which would be surprising and worth stating, since Tier 1 at 21 queries found 17.29% → 13.29% |
| H3 | Conformal bounds achieve nominal **conditional** coverage per machine once calibration groups are plentiful | Report that per-machine conditional coverage is unattainable, and that SLA gating must therefore rest on abstention rather than on a guarantee |
| H4 | Plan divergence is a major term in cross-environment error | Already leaning rejected: Tier 3 measured ~16% and an *inverted* correlation with error (r = −0.551, p = 0.010). Report it as a refuted intuition |
| H5 | A learned model beats the analytical formula out of hull | Already leaning rejected: 23.90% vs 21.61% leave-one-machine-out. Report that formulas extrapolate and learned models interpolate |

## Fixed in advance

- **Metrics**: MAPE, MdAPE, and q-error (median and p95). All reported, every time. No
  metric will be selected after the fact.
- **Protocol**: leave-one-machine-out as primary; leave-one-query-out and
  leave-one-machine-and-query-out reported alongside. Classifiers refit inside every fold.
- **Significance**: paired cluster bootstrap at the machine level, 2000 resamples, seed 42.
  A result is "significant" only if the 95% CI excludes zero.
- **Warmup**: repetition 0 of every cell is discarded. Non-negotiable — at n=20 repetitions
  the cold first run lands exactly where p95/p99 sit, and it inflated 46 of 105 p99 values by
  more than 5%.
- **Canonical numbers**: every published figure comes from `make results`, which is
  deterministic and verified byte-identical across runs.

## What would falsify the whole approach

If, at ≥30 decorrelated configurations, **no** method beats "assume production equals
development" by a significant margin, the premise
`runtime = workload cost × hardware capability` is not useful at this granularity, and the
honest output is a negative-result paper plus the public dataset. That outcome is accepted in
advance.

## Conflicts and limits declared up front

- Container CPU quotas vary *capability* without varying *microarchitecture*; caches and the
  memory controller stay shared. Conclusions about signature dimensionality transfer;
  conclusions about absolute runtime on real small machines do not.
- The STREAM bandwidth probe behind the five frozen EC2 signatures is lost, so those machines
  cannot be re-measured on the new scale and will be reported separately.
- All measurements are PostgreSQL. Engine version alone shifts runtime ~30% and changes
  12/22 plans, so results are conditional on a fixed version.
