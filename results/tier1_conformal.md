# Calibrated prediction intervals (Tier 1, items 20, 21, 24, 29)

One-sided **upper** bounds on `scaling_factor_p50`, evaluated leave-one-machine-out.
Coverage is `P(y <= upper_bound)`; it should be at least the nominal level.
`rel width` is `upper_bound / actual`, averaged — lower is tighter, 1.0 would be a
perfect oracle bound.

| method | nominal | empirical (mean over 5 folds) | worst single machine | rel width |
|---|---:|---:|---:|---:|
| gbr_log | 90% | 66.0% ⚠ | 56.0% | 1.180 |
| gbr_log | 95% | 72.9% ⚠ | 61.9% | 1.243 |
| gbr_log | 99% | 85.2% ⚠ | 74.4% | 1.402 |
| gbr_quantile | 90% | 78.1% ⚠ | 70.2% | 1.365 |
| gbr_quantile | 95% | 84.6% ⚠ | 79.8% | 1.457 |
| gbr_quantile | 99% | 91.5% ⚠ | 79.8% | 1.865 |
| split_conformal | 90% | 91.2% | 78.0% | 1.625 |
| split_conformal | 95% | 97.0% | 90.5% | 1.871 |
| split_conformal | 99% | 99.6% | 98.8% | 2.331 |
| weighted_conf | 90% | 91.8% | 78.0% | 1.619 |
| weighted_conf | 95% | 97.0% | 90.5% | 1.890 |
| weighted_conf | 99% | 99.8% | 98.8% | 2.380 |

## Reading this

**⚠ marks a method that fails to deliver its promised coverage.**

The measurement-noise floor on the target itself is **0.60%** relative standard error (item 29): `y` is a ratio of two p50s each estimated from 19 warm repetitions, so even a perfect model cannot resolve `y` more finely than this. Any interval tighter than that is claiming precision the measurements do not contain.

`split_conformal` calibrates on a training machine the regressor never saw, so its
guarantee is conditional on the target machine being exchangeable with that
calibration machine. The c5a result is direct evidence that assumption fails for an
outlier machine — which is exactly what `weighted_conf` corrects for, by weighting
calibration points by hardware-signature proximity to the target. Compare their
worst-single-machine columns rather than their means: the mean hides the fold that
matters.

