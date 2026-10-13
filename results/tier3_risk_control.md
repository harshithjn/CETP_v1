# N4: conformal risk control for the SLA gate (Tier 3, item 42)

A deployment owner states a risk budget — *"no more than 5% of the queries I approve may breach the SLA"* — and this derives the gate threshold that honours it, with a finite-sample guarantee, instead of hand-tuning a constant.

The gate approves when `lambda * predicted_scaling <= tau`. `lambda` is calibrated on the four training machines by conformal risk control and then tested on the held-out machine, which the calibration never saw.

## Predictor: `cmp_only`

| tau | risk budget | lambda | approved | realised risk | breach rate among approved | budget met |
|---:|---:|---:|---:|---:|---:|---|
| 1.2 | 1% | 1.44 | 30.0% | 2.02% | 5.5% | **no** (worst fold 7.1%) |
| 1.2 | 5% | 1.17 | 57.5% | 7.14% | 11.5% | **no** (worst fold 14.3%) |
| 1.2 | 10% | 1.08 | 62.5% | 10.24% | 14.0% | **no** (worst fold 26.8%) |
| 1.5 | 1% | 1.64 | 42.5% | 2.02% | 2.7% | **no** (worst fold 8.9%) |
| 1.5 | 5% | 1.00 | 92.5% | 8.33% | 8.5% | **no** (worst fold 26.8%) |
| 1.5 | 10% | 0.87 | 95.0% | 9.76% | 9.9% | **no** (worst fold 26.8%) |
| 2.0 | 1% | 1.18 | 95.0% | 1.79% | 1.8% | **no** (worst fold 7.7%) |
| 2.0 | 5% | 0.65 | 100.0% | 3.10% | 3.1% | **no** (worst fold 7.7%) |
| 2.0 | 10% | 0.50 | 100.0% | 3.10% | 3.1% | yes |

## Predictor: `analytical`

| tau | risk budget | lambda | approved | realised risk | breach rate among approved | budget met |
|---:|---:|---:|---:|---:|---:|---|
| 1.2 | 1% | 1.42 | 27.3% | 1.67% | 5.5% | **no** (worst fold 4.2%) |
| 1.2 | 5% | 1.25 | 44.4% | 5.48% | 11.2% | **no** (worst fold 11.3%) |
| 1.2 | 10% | 1.11 | 63.1% | 11.67% | 16.6% | **no** (worst fold 26.8%) |
| 1.5 | 1% | 1.60 | 42.9% | 1.55% | 1.9% | **no** (worst fold 6.5%) |
| 1.5 | 5% | 0.90 | 92.9% | 8.57% | 8.8% | **no** (worst fold 26.8%) |
| 1.5 | 10% | 0.87 | 95.0% | 9.76% | 9.9% | **no** (worst fold 26.8%) |
| 2.0 | 1% | 1.16 | 94.0% | 1.79% | 1.8% | **no** (worst fold 7.7%) |
| 2.0 | 5% | 0.64 | 100.0% | 3.10% | 3.1% | **no** (worst fold 7.7%) |
| 2.0 | 10% | 0.50 | 100.0% | 3.10% | 3.1% | yes |

## The guarantee does not survive an unseen machine

**16 of 18 configurations exceed their stated risk budget on the held-out machine.** The thresholds are calibrated correctly — on the calibration machines the bound holds by construction — but the realised risk on a machine the calibration never saw routinely overshoots, in the worst cases by several times the budget.

This is not an implementation defect. Conformal risk control guarantees its bound **under exchangeability** between calibration and test data. A held-out machine is not exchangeable with the training machines — that is precisely the finding the c5a outlier established in Phase 7 and that Tier 1 reproduced. The guarantee is void in exactly the setting the product cares about, and the honest contribution here is **measuring by how much it is void** rather than reporting the calibration as a success.

Tier 1 found the same thing from the coverage side: marginal coverage held on average (97.0% at nominal 95%) while conditional coverage on the worst machine failed (90.5%). Two independent formulations, same conclusion. Weighting by hardware distance was tried there and bought nothing, because four calibration machines give only four distinct weights.

At tau = 1.5 the base breach rate is 12.9%, and the derived multipliers are:

- a **1%** budget needs `lambda = 1.64`, approves **42.5%** of candidates, and realises 2.02% mean risk (**over budget**, worst fold 8.9%)
- a **5%** budget needs `lambda = 1.00`, approves **92.5%** of candidates, and realises 8.33% mean risk (**over budget**, worst fold 26.8%)
- a **10%** budget needs `lambda = 0.87`, approves **95.0%** of candidates, and realises 9.76% mean risk (**over budget**, worst fold 26.8%)

## What is nonetheless worth keeping

The machinery is right and the framing is right: a deployment owner states a risk budget, and a threshold is *derived* rather than chosen. Compare that with the shipped gate's hand-set 0.6 confidence constant, which Tier 1 showed could not even be tuned at n=5 machines because the score takes one value per machine. Here the threshold is calibrated on *pairs*, of which there are hundreds, so it is at least estimable.

The gap is exchangeability, and it is a **data problem, not a method problem**. With 30+ machines spanning the hardware space, a target machine is far more plausibly exchangeable with its calibration set, and weighted CRC under covariate shift gets enough distinct weights to matter. Until then, a stated risk budget on unseen hardware should be reported as *aspirational*, and the gate should lean on abstention rather than on a guarantee it cannot honour.

## Honest limitations

- **The controlled quantity is the rate of wrongly approved deployments over ALL candidates**, not the breach rate *among* approved ones. The latter is the more natural reading of the sentence, but it is not monotone in lambda and so cannot be controlled by this method. Both columns are above; the gap between them is large at tight budgets.
- 5 machines means 4 calibration machines per fold. Every caveat about small-sample conformal applies.
- At loose budgets the derived `lambda` falls below 1.0 (e.g. 0.87 at a 10% budget), i.e. CRC asks the gate to be *less* conservative than the raw prediction and approves nearly everything. That is correct behaviour for a loose budget, but it means the gate stops being a gate.

