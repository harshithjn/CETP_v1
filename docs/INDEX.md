# Documentation index

Every Markdown document in this repository, rendered to PDF under `docs/`.
The tree is mirrored, so `results/CANONICAL.md` is at `docs/results/CANONICAL.pdf`.

Regenerate with `make docs-all`. Sources are the Markdown files; **the PDFs are
build output and should not be edited.**

## Start here

| Document | PDF | About |
|---|---|---|
| `RUNNING.md` | [RUNNING.pdf](RUNNING.pdf) | **Start here to run it.** Install, verify, predict, reproduce, troubleshoot |
| `README.md` | [README.pdf](README.pdf) | Repository overview and the headline result |
| `docs/HANDBOOK.md` | [HANDBOOK.pdf](HANDBOOK.pdf) | **Start here to understand it.** A-to-Z: concepts, findings, history, limits |
| `docs/LLD.md` | [LLD.pdf](LLD.pdf) | Low-Level Design. Architecture, the model, results, limitations |

## Results (generated — never hand-edited)

| Document | PDF | About |
|---|---|---|
| `results/CANONICAL.md` | [results/CANONICAL.pdf](results/CANONICAL.pdf) | **The source of truth** for the original five-machine dataset |
| `results/addition1_resolution_and_addition2_report.md` | [results/addition1_resolution_and_addition2_report.pdf](results/addition1_resolution_and_addition2_report.pdf) | Addition 1 Resolution + Addition 2: Learning-Curve Experiment |
| `results/addition4_compute_benchmark_report.md` | [results/addition4_compute_benchmark_report.pdf](results/addition4_compute_benchmark_report.pdf) | Addition 4: Repairing the Compute Microbenchmark and Re-Evaluating |
| `results/addition4b_mechanism_report.md` | [results/addition4b_mechanism_report.pdf](results/addition4b_mechanism_report.pdf) | Addition 4b: Verifying the Parallelism Mechanism |
| `results/addition7_online_loop_report.md` | [results/addition7_online_loop_report.pdf](results/addition7_online_loop_report.pdf) | Addition 7: Live On-the-Go Self-Calibrating Loop |
| `results/buffer_bug_correction_report.md` | [results/buffer_bug_correction_report.pdf](results/buffer_bug_correction_report.pdf) | Buffer-Bug Dataset Correction |
| `results/concurrency.md` | [results/concurrency.pdf](results/concurrency.pdf) | Concurrency elasticity, fitted and cross-validated |
| `results/confidence_abstention_report.md` | [results/confidence_abstention_report.pdf](results/confidence_abstention_report.pdf) | Addition 3: Confidence-Aware Abstention |
| `results/cross_isa_operator_rates.md` | [results/cross_isa_operator_rates.pdf](results/cross_isa_operator_rates.pdf) | Why the failure is not operator mix |
| `results/cross_isa_prediction.md` | [results/cross_isa_prediction.pdf](results/cross_isa_prediction.pdf) | The cross-architecture failure and its repair |
| `results/final_fixes_report.md` | [results/final_fixes_report.pdf](results/final_fixes_report.pdf) | Final Technical Improvements: Three Fixes |
| `results/laptop_to_ec2.md` | [results/laptop_to_ec2.pdf](results/laptop_to_ec2.pdf) | The 59.7% ceiling on scalar capability models |
| `results/laptop_to_prod.md` | [results/laptop_to_prod.pdf](results/laptop_to_prod.pdf) | Laptop to eleven production machines |
| `results/online_predictor_report.md` | [results/online_predictor_report.pdf](results/online_predictor_report.pdf) | Online Self-Calibrating Prediction: Results |
| `results/operator_model.md` | [results/operator_model.pdf](results/operator_model.pdf) | The capability grid and the ablations |
| `results/phase7_report.md` | [results/phase7_report.pdf](results/phase7_report.pdf) | Cross-Environment TPC-H Runtime Prediction: Final Results and Limitations |
| `results/phase8_cetp_gate_demo.md` | [results/phase8_cetp_gate_demo.pdf](results/phase8_cetp_gate_demo.pdf) | Phase 8 CETP Gate — Worked Demo |
| `results/q15_resolution.md` | [results/q15_resolution.pdf](results/q15_resolution.pdf) | Q15 resolved (Tier 1, item 28) |
| `results/tier1_ablation.md` | [results/tier1_ablation.pdf](results/tier1_ablation.pdf) | Classifier ablation (Tier 1, item 19) |
| `results/tier1_conformal.md` | [results/tier1_conformal.pdf](results/tier1_conformal.pdf) | Calibrated prediction intervals (Tier 1, items 20, 21, 24, 29) |
| `results/tier1_elasticity.md` | [results/tier1_elasticity.pdf](results/tier1_elasticity.pdf) | Measured resource elasticity (Tier 1, items 17 and 18) |
| `results/tier1_gate.md` | [results/tier1_gate.pdf](results/tier1_gate.pdf) | Risk-derived threshold and a conformal SLA gate (Tier 1, items 22, 23) |
| `results/tier1_hw_signature.md` | [results/tier1_hw_signature.pdf](results/tier1_hw_signature.pdf) | Is a two-scalar hardware signature enough? (Tier 1, item 26) |
| `results/tier1_laptop_dev.md` | [results/tier1_laptop_dev.pdf](results/tier1_laptop_dev.pdf) | arm64 laptop as the dev machine (Tier 1, item 27) |
| `results/tier1_plan_features.md` | [results/tier1_plan_features.pdf](results/tier1_plan_features.pdf) | Do plan-structure features help? (Tier 1, item 25) |
| `results/tier2_concurrency.md` | [results/tier2_concurrency.pdf](results/tier2_concurrency.pdf) | Concurrency sensitivity (Tier 2, item 35) |
| `results/tier2_versions.md` | [results/tier2_versions.pdf](results/tier2_versions.pdf) | PostgreSQL version sensitivity (Tier 2, item 36) |
| `results/tier3_cold_start.md` | [results/tier3_cold_start.pdf](results/tier3_cold_start.pdf) | N3: budgeted cold start (Tier 3, item 41) |
| `results/tier3_plan_drift.md` | [results/tier3_plan_drift.pdf](results/tier3_plan_drift.pdf) | N1: plan drift (Tier 3, item 39) |
| `results/tier3_risk_control.md` | [results/tier3_risk_control.pdf](results/tier3_risk_control.pdf) | N4: conformal risk control for the SLA gate (Tier 3, item 42) |
| `results/tier4_plan_drift_causal.md` | [results/tier4_plan_drift_causal.pdf](results/tier4_plan_drift_causal.pdf) | Causal plan-drift decomposition (item 3) |
| `results/tier4_version_transfer.md` | [results/tier4_version_transfer.pdf](results/tier4_version_transfer.pdf) | Engine version as a transfer axis (item 5) |

## Reference

| Document | PDF | About |
|---|---|---|
| `CONFERENCE_PLAN.md` | [CONFERENCE_PLAN.pdf](CONFERENCE_PLAN.pdf) | Publication plan and venue analysis |
| `DATASHEET.md` | [DATASHEET.pdf](DATASHEET.pdf) | Datasheet for the dataset (Gebru et al. format) |
| `DOC_CORRECTIONS.md` | [DOC_CORRECTIONS.pdf](DOC_CORRECTIONS.pdf) | Itemised errors in the superseded deck and LLD |
| `IMPLEMENTATION_REFERENCE.md` | [IMPLEMENTATION_REFERENCE.pdf](IMPLEMENTATION_REFERENCE.pdf) | Implementation notes |
| `OPERATOR_MODEL.md` | [OPERATOR_MODEL.pdf](OPERATOR_MODEL.pdf) | Design and status of the per-operator work; what each move is worth |
| `OVERNIGHT_REPORT.md` | [OVERNIGHT_REPORT.pdf](OVERNIGHT_REPORT.pdf) | Report from the unattended run of 2026-10-13 |
| `PREREGISTRATION.md` | [PREREGISTRATION.pdf](PREREGISTRATION.pdf) | What was committed to before the Tier-3 experiments ran |
| `QUICKSTART.md` | [QUICKSTART.pdf](QUICKSTART.pdf) | Condensed usage for the installed `cetp` command |
| `RELATED_WORK.md` | [RELATED_WORK.pdf](RELATED_WORK.pdf) | Prior art, verified against publishers rather than quoted from memory |
| `ROADMAP.md` | [ROADMAP.pdf](ROADMAP.pdf) | Longer-term plan |
| `TASKS.md` | [TASKS.pdf](TASKS.pdf) | Full history: everything done, everything that failed, everything open |
| `TIER1_RESULTS.md` | [TIER1_RESULTS.pdf](TIER1_RESULTS.pdf) | Tier 1 summary — method |
| `TIER2_RESULTS.md` | [TIER2_RESULTS.pdf](TIER2_RESULTS.pdf) | Tier 2 summary — dataset |
| `TIER3_RESULTS.md` | [TIER3_RESULTS.pdf](TIER3_RESULTS.pdf) | Tier 3 summary — research questions |
| `data/raw_buggy_do_not_use/README.md` | [data/raw_buggy_do_not_use/README.pdf](data/raw_buggy_do_not_use/README.pdf) | DO NOT USE — superseded, contains a known data bug |
| `models/MODEL_CARD.md` | [models/MODEL_CARD.pdf](models/MODEL_CARD.pdf) | What the shipped artifacts are, and are not |
| `scripts/collection/queries/README.md` | [scripts/collection/queries/README.pdf](scripts/collection/queries/README.pdf) | TPC-H queries — reconstructed 2026-10-12 |

