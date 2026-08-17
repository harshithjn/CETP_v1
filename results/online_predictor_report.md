# Online Self-Calibrating Prediction: Results

## Confidence-score definition

`confidence = min(interval_component, hw_region_component, query_support_component)` — a weakest-link combination: any single strong reason for doubt caps overall confidence, rather than letting two easy signals dilute one concerning one (an equal-weight average was tried first and rejected for exactly this reason — see Design Note below).

- **interval_component** = `1 / (1 + (p99-p50)/max(p50, 0.05))`. Computed from the current scaling model's own p50/p95/p99 predictions for this request. Wide interval → lower score.
- **hw_region_component** = `1 - min_dist(prod_signature, seen_machine_signatures) / sqrt(2)`. `prod_signature` is the static, pre-measured (bandwidth, compute) tuple for the prod machine, min-max normalized against the known 5-machine universe. Distance is to the nearest machine with any rows currently in the training pool. This is the direct encoding of the Phase 7 c5a lesson: a prod machine whose hardware profile sits far from anything the model has trained on scores low here, regardless of how tight the model's own interval looks.
- **query_support_component** = 1.0 if `dev_machine` has contributed training rows already, else 0.5 if some other query of the same Phase-5 bottleneck class has been trained on, else 0.0.

**Threshold = 0.6** (≥ → PREDICT, < → MEASURE). **Retrain trigger N = 10** newly measured rows, exposed as an explicit `retrain_if_ready()` call, not an automatic side effect of measuring.

All three components are computed from dev-side features, static hardware signatures, and the model's own outputs — never from `prod_time`. Confirmed no leakage into the gate itself (same audit standard as the Phase 6/7 pre-pipeline validation).

## Simulation setup

Initial seen machines: **c7i, m5a, r5n** (chosen deliberately, not the first three alphabetically) so that the two streamed-in machines are **c5a** — the known Phase 7 hardware outlier (highest bandwidth, lowest compute in the 5-machine set) — and **z1d** — a machine whose normalized signature sits close to c7i's. This setup directly tests whether the gate reproduces the Phase 7 finding (c5a should look risky; z1d should look comparatively safe) rather than just picking an arbitrary split.

294 remaining (dev, prod, query) triples streamed in a fixed, seeded random order (seed 42).

## Headline result: the gate does NOT improve predicted-subset accuracy

| | value |
|---|---|
| Predicted-subset MAPE (rows the gate chose to predict) | **21.70%** |
| Blind-all MAPE (same model, predicting on every streamed row regardless of gate) | **18.10%** |

**The predicted-subset MAPE is worse, not better, than blind prediction.** This is the honest negative finding the task anticipated. Confirmed directly: correlation between the gate's confidence score and the model's actual absolute-percentage error across the full stream is **r = 0.084** — essentially uncorrelated. Rows the gate routed to MEASURE had a *lower* mean blind error (16.5%) than rows it chose to PREDICT (21.7%) — the gate is not discriminating hard cases from easy ones in the direction intended.

## Measurement-cost curve: the "money result" did not materialize

| | measured rate |
|---|---|
| First half of stream | 55.1% |
| Second half of stream | 83.7% |

Measurement need **increased**, not decreased, over the course of the stream — the opposite of the intended self-calibrating trajectory. Predicted count: 90/294 (30.6%); measured: 204/294 (69.4%).

## Diagnosis: which component is responsible

Decomposing the three components (logged per-row) shows a clean split:

- **hw_region_component behaves exactly as designed.** For c5a-involving requests it starts at ≈0.42 (correctly below threshold, correctly flagging c5a as hardware-novel) and jumps to 1.0 immediately after the *first* c5a row is ever measured — correctly encoding "we've now seen this hardware region at all," not "we've seen a lot of it." z1d shows the same pattern but starts closer to safe territory, consistent with its closer resemblance to c7i in signature space. **This component is a validated success and reproduces the Phase 7 c5a lesson exactly as intended.**
- **interval_component is the culprit**, and it is volatile rather than convergent. Under batch retraining every 10 measured rows on a still-small pool (peaking around 330 rows by the end of the stream), the quantile GBR's p50-to-p99 spread does not shrink smoothly — later in the stream, interval_component values as low as 0.14–0.25 appear for c5a rows that had scored above 0.9 only a few dozen steps earlier. This is the same small-sample tail-quantile instability already documented in Phase 6/7 (p95/p99 calibration under-covers even on the full 5-machine, 420-row dataset); here, with a periodically-retrained pool that's smaller still, it is worse and non-monotonic.

**Ablation check:** removing interval_component entirely (confidence = min(hw_region, query_support) only) does make the measured-rate curve "look" like it decreases — it collapses to near-zero measurement (0.7% of the stream) almost immediately after each new machine's first observation. But this is not an improvement; it is the gate becoming permissive for the wrong reason. With no live check on individual prediction quality, it blindly trusts every subsequent prediction for a "known" machine regardless of whether that specific query's prediction is any good, and the resulting blind-all MAPE for that run (≈41%) is markedly worse than the full three-component gate's blind-all MAPE (18.10%). **Removing the noisy signal produces a nicer-looking curve and a worse, silently-overconfident system — keeping it, despite its poor correlation with true error, is the more defensible choice for a safety-oriented gate**, even though it does not deliver the hoped-for measurement-decay result.

## Honest summary

- The **hardware-region-support signal works exactly as designed** and is a genuine, isolated success: it correctly distinguishes a hardware outlier (c5a) from a merely-new-but-nearby machine (z1d), directly operationalizing the Phase 7 finding.
- The **combined confidence gate does not improve predicted-subset accuracy over blind prediction** (21.70% vs 18.10% MAPE) and **measurement need did not decrease over the stream** (55.1% → 83.7%) — both contrary to the intended design goal. Report this plainly rather than tuning further to force a nicer-looking curve.
- The root cause is traceable to **interval_component**, whose near-zero correlation with true error (r=0.084) reflects the same small-sample quantile-calibration weakness already flagged in Phase 6/7, now compounded by periodic small-batch retraining that does not converge smoothly.
- Given the choice between an honest-but-unflattering gate (current design) and a superficially-improving-but-more-dangerous one (drop the noisy signal), we kept the honest one. **This is a legitimate scope for future work — replacing interval-width with a more stable confidence proxy, or accumulating a larger initial pool before enabling the self-calibration loop — not a wiring defect in the current implementation.**

## Artifacts

- `online_predictor.py` — confidence gate, retrain trigger, simulation harness.
- `results/online_predictor_simulation_log.csv` — full per-request log (294 rows): decision, confidence, all three components, predicted vs actual scaling factor.
- `results/online_predictor_summary.json` — aggregate stats.
