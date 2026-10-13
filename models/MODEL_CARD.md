# Model card

**Regenerated 2026-10-13.** The three-way staleness documented here previously is resolved:
the artifacts are now trained on the buffer-corrected dataset, pickled with the pinned
scikit-learn, and carry the multi-core compute signal the headline result requires.

| was | now |
|---|---|
| trained on the buffer-bug dataset | trained on `data/corrected/tpch_dataset_corrected_v2.csv` |
| pickled with scikit-learn 1.6.1 against a 1.8.0 pin | pickled with the pinned version |
| single-core compute scores (correlate +0.213, **wrong sign**) | multi-core aggregate GFLOPS (correlate −0.691) |

Previous artifacts are kept in `models_backup/`. `gate/cetp.yml` and the demo scenarios in
`gate/cetp_gate_demo.py` were updated to the same scale, because a gate whose config
disagrees with its models is worse than one that is merely stale.

**28/28 tests pass.** Five locked prediction values and the four demo scenarios changed as a
direct consequence and were each reviewed before being updated — see the note above
`KNOWN_ANSWER_TRIPLES` in `tests/test_e2e.py`.

## What is still true, and matters

**The compute axis is probed at `nproc`, not at the achievable degree of parallelism.** A
PostgreSQL query can use at most `1 + max_parallel_workers_per_gather` = 3 processes, so a
figure aggregated over every core describes capacity nothing can reach. For c5a and m5a
(4 vCPU) this overstates usable compute; r5n, z1d and c7i (2 vCPU) are already DOP-matched by
accident. Measured cost of the mistake on an 8-core machine: **324% vs 153% MAPE**
(`results/tier1_laptop_dev.md`).

It is not fixed here because the five EC2 instances are terminated and cannot be re-probed.
**New machines must use `scripts/collection/hw_signature.py`**, which reads the setting from
the server and probes accordingly, and records the result in
`models/hardware_signatures.json`. `cetp.signature.ratios()` refuses to compare a machine from
that registry with one of these five, because the scales differ.

**The bottleneck classifier contributes nothing measurable.** Leave-one-query-out macro-F1 is
0.375 with `io`-class recall of 0.000, against a 0.161 trivial baseline — and a flat uniform
weighting scores 21.55% MAPE against this classifier's 21.61%. It is retained because
`gate/cetp_gate.py` and its tests expect it, not because it earns its place.

**The p95/p99 training targets are warmup-contaminated.** At 20 repetitions per cell the cold
first run lands exactly where those percentiles sit; 46 of 105 p99 values were inflated by
more than 5%, up to 6.92×. Use `models/calibration.json` for intervals instead.

## What the installable package uses instead

`cetp.predict` **does not load any of these pickles.** It ships the analytical formula plus
`models/calibration.json`, because leave-one-machine-out the formula is more accurate out of
hull (analytical 21.61%, `1/compute_ratio` 20.06%, learned GBR 23.90%) and because a formula
cannot go stale. The pickles exist for `gate/cetp_gate.py`, which is retained for continuity
with its 28 tests.
