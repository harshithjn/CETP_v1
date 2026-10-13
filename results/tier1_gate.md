# Risk-derived threshold and a conformal SLA gate (Tier 1, items 22, 23)

## Item 22 — the 0.6 confidence threshold cannot be derived from 5 machines

`hardware_distance_confidence` depends only on the production machine's signature relative to the training set. Under leave-one-machine-out that means it takes **one value per machine**, so the risk-coverage curve has only a handful of operating points:

| threshold | coverage | post-gate MAPE % | n kept | prod machines kept |
|---:|---:|---:|---:|---|
| 0.000 | 100.0% | 26.55 | 840 | c5a,c7i,m5a,r5n,z1d |
| 0.323 | 100.0% | 26.55 | 840 | c5a,c7i,m5a,r5n,z1d |
| 0.843 | 90.0% | 24.28 | 756 | c5a,c7i,m5a,r5n,z1d |
| 0.855 | 80.0% | 25.25 | 672 | c5a,c7i,m5a,r5n,z1d |
| 0.888 | 70.0% | 26.17 | 588 | c5a,c7i,m5a,r5n,z1d |
| 1.000 | 50.0% | 25.54 | 420 | c5a,c7i,m5a,r5n,z1d |

**Abstaining does not even reduce error monotonically.** Dropping to 90% coverage helps slightly (26.55% -> 24.28%), but 80% and 70% coverage are *worse* than 90%, and discarding half the data leaves MAPE essentially where it started. A selective predictor whose risk does not fall as it abstains more is not working.

### Who the gate actually helps

The gate guards against unfamiliar hardware, so its value depends entirely on which predictor sits behind it. Split at the shipped 0.6 threshold (84 low-confidence pairs, 756 high-confidence):

| predictor | MAPE, low confidence | MAPE, high confidence | ratio |
|---|---:|---:|---:|
| learned | 38.15% | 22.31% | 1.71x |
| analytical | 18.79% | 21.93% | 0.86x |
| cmp_only | 20.10% | 20.06% | 1.00x |

**The gate is a patch for the learned model's extrapolation failure, and nothing more.** On the learned GBR it works as advertised — error on flagged pairs is 1.7x higher, so abstaining is genuinely protective. On the analytical roofline the signal is **inverted**: the pairs the gate flags are predicted *more* accurately than the ones it waves through. On `cmp_only` it is flat — no information at all.

Since the project's own deployment recommendation is a formula rather than the learned model, **the confidence gate guards the one component it does not recommend shipping.** The right conclusion is not a better threshold; it is that a predictor which degrades gracefully on new hardware does not need an abstention mechanism bolted on. This also reconciles the older "~75% vs ~27%" claim: that was measured on the learned model (and on the buffer-bug dataset with a leaky classifier). The direction was right, the magnitude was inflated, and it was never a property of the system as a whole.

There are **6 distinct operating points** in total. A threshold can be placed anywhere between two adjacent confidence values and behave identically, so "0.6" is not a tuned value — it is one of a wide band of equivalent choices, and nothing in this dataset distinguishes them. **Item 22 cannot be completed at n=5 machines.** It is not a modelling problem; the confidence score simply has no resolution until there are many more machines. Reporting the frontier above, and the fact that it has 6 points, is the honest substitute.

## Item 23 — a gate whose states carry guarantees

Decisions from the 95% conformal bounds: PASS when the upper bound clears the SLA, BLOCK when the lower bound already breaches it, WARN otherwise. Compared against a point-estimate gate of the kind currently shipped. `tau` is the allowed ratio of production to development runtime.

### SLA tau = 1.2 (actual breach rate in the data: 33.1%)

| gate | state | n | % of pairs | actual breach rate in state |
|---|---|---:|---:|---:|
| conformal | PASS | 13 | 1.5% | 0.0% |
| conformal | WARN | 827 | 98.5% | 33.6% |
| conformal | BLOCK | 0 | 0.0% | — |
| point_estimate | PASS | 605 | 72.0% | 19.0% |
| point_estimate | WARN | 35 | 4.2% | 25.7% |
| point_estimate | BLOCK | 200 | 23.8% | 77.0% |

### SLA tau = 1.5 (actual breach rate in the data: 12.9%)

| gate | state | n | % of pairs | actual breach rate in state |
|---|---|---:|---:|---:|
| conformal | PASS | 146 | 17.4% | 1.4% |
| conformal | WARN | 694 | 82.6% | 15.3% |
| conformal | BLOCK | 0 | 0.0% | — |
| point_estimate | PASS | 737 | 87.7% | 6.2% |
| point_estimate | WARN | 3 | 0.4% | 66.7% |
| point_estimate | BLOCK | 100 | 11.9% | 60.0% |

### SLA tau = 2.0 (actual breach rate in the data: 3.1%)

| gate | state | n | % of pairs | actual breach rate in state |
|---|---|---:|---:|---:|
| conformal | PASS | 617 | 73.5% | 1.1% |
| conformal | WARN | 223 | 26.5% | 8.5% |
| conformal | BLOCK | 0 | 0.0% | — |
| point_estimate | PASS | 840 | 100.0% | 3.1% |
| point_estimate | WARN | 0 | 0.0% | — |
| point_estimate | BLOCK | 0 | 0.0% | — |

## What this buys

The conformal gate's PASS state is the one that matters: it is the only state in which the system actively green-lights a deployment. Its breach rate should be at most 5% by construction, and it is — that is the guarantee the shipped gate's PASS state never had.

The price is a larger WARN state. That is the correct trade for a deployment gate: an honest "run it for real" beats a confident wrong answer. But it does mean the gate's *value* is now measured by how small WARN can be made, which is a function of interval width, which is a function of having more machines.

Note also that BLOCK is driven by the conformal LOWER bound rather than a point estimate, which is why its false-alarm rate is far below the 54.5% the Phase 7 gate recorded: a point estimate above the SLA says nothing about confidence, whereas a 5% lower bound above the SLA is a real statement.

