# 06 — Gaps, contradictions, risks, traceability

Severity: H/M/L. Conf = confidence that the issue is real as stated.

## C. Confirmed contradictions (evidence in repo)
| ID | Issue | Evidence | Sev | Conf | Check / fix |
|---|---|---|---|---|---|
| C1 | **Stale "known inconsistency"**: CANONICAL.md §7 (hard-coded text in the generator) and README "Limitations" say `hardware_signature.pkl` and `gate/cetp.yml` carry single-core scores. Both actually carry multi-core values (c5a 40.8071, c7i 25.8909). PROVENANCE.json also says resolved. | `gate/cetp.yml:1-14`; `joblib.load('models/hardware_signature.pkl')` printed; `PROVENANCE.json` | M | High (V) | Update generator text + README; the regenerated CANONICAL.md will keep printing the false statement until `canonical_evaluation.py` is edited |
| C2 | README says "28 tests"; actual suite is **108** (28 e2e + 42 + 38). `make test` runs only the 28. CANONICAL §7 says "24 tests". | pytest run | L | High (V) | Update counts; point `make test` at `tests/` |
| C3 | Cross-ISA headline numbers differ by source: README/`predict` docs cite 324 % vs 123 %; `laptop_to_ec2_metrics.json` shows 337.75 % (cmp_nproc8), 160.68 % (cmp_dop3), naive 129.39 %. Another doc cites "153 %". | `results/laptop_to_ec2_metrics.json` | M | Med | Identify which query/machine subset yields 324/123; may be a pre-fix run |
| C4 | `cross_env.py` warning hard-codes "laptop → 6 container machines, 132 pairs: 28.54 % vs 38.22 %"; committed metrics give **7 targets, 29.31 % vs 37.39 %** and an 11-cell/242-pair aggregate of **43.72 % vs 31.70 %**. Shipped warning text is stale and the aggregate shows zero-shot operator model losing to naive. | `cross_env.py:241-246`; `laptop_to_prod_metrics.json` | M | High | Regenerate warning from metrics; report 11-cell aggregate |
| C5 | Gen-2 headline "24.95 % vs 72.52 %" includes 4 of 8 cells the project itself flags `reliable: False`; per-cell, operator ≈ naive or worse on 5 of 7 same-ISA targets. Aggregate is driven by the two 1-CPU cells. | `operator_model_metrics.json`, `per_prod_machine` | M | High | Always quote with reliable-subset (22.23 vs 74.99) and per-cell table |
| C6 | Docs pipeline broken by design: `docs/INDEX.md` and `Makefile` say PDFs are generated from Markdown sources; Markdown deleted in `38c4bf2`; code comments still cite `results/*.md`, `models/MODEL_CARD.md`, `TASKS.md`, `QUICKSTART.md` that no longer exist. `make docs-all` would have nothing to build. | `git ls-files`, comments in `cross_env.py`, `signature.py`, `calibration_store.py` | M | High | Decide source of truth; restore Markdown or retarget comments |
| C7 | README has a broken link `[[…CANONICAL.pdf](…)](…)`, a dangling "Then open `http://localhost:8000`" code fence left from the removed dashboard, and says `results/CANONICAL.md` is produced though it is untracked. | README | L | High | cosmetic |
| C8 | Paper abstract: "22 TPC-H queries … thirteen environments"; Gen-1 canonical uses 21 queries/5 machines; README headline is Gen-1 while its banner says Gen-1 is replaced. Two incompatible "headline results". | README, abstract, CANONICAL | M | High | Pick one headline narrative per document |
| C9 | `cetp-gate.yml` runs the legacy pickle gate on c7i→c5a *demo* queries; "BLOCK" is tolerated by design — CI green does not validate predictions. | workflow | L | High | — |

## R. Risks / possible issues (not confirmed as defects)
| ID | Risk | Evidence | Sev |
|---|---|---|---|
| R1 | Hardware-distance confidence in `predict()` always uses the 5 EC2 LEGACY machines as reference, so registry or Gen-2 machines always score low-confidence/out-of-hull | `predict.py:131` | M |
| R2 | `redact` default salt `"cetp"` is public; `structural` level leaves schema names; regex-based, may miss literals in unusual fields | `redact.py` | M |
| R3 | Calibration store and telemetry lock-free; concurrent writers can lose updates (telemetry `resolve` rewrites whole file) | `telemetry.py`, `calibration_store.py` | L |
| R4 | Probe validity depends on idle machine; GHA runners noisy; only a repeatability gate protects | workflow comment, `load_rates` | M |
| R5 | EC2 raw data/hosts gone; bandwidth probe source "lost" — Gen-1 signature unreproducible for new machines | `signature.py` | M |
| R6 | No managed-DB (RDS/Aurora) measurement although it is the stated motivation for SQL probes | absence in results list | M |
| R7 | Tests: 108 pass, but test bodies not audited; many locked "known answer" values were re-derived after retraining (PROVENANCE note) — they pin behavior, not truth | PROVENANCE | L |
| R8 | 23k DeprecationWarnings from NumPy 2.5 + joblib unpickling; future NumPy may break pickle loading | pytest output | L |
| R9 | README/limitations: classifier is "effectively a 21-row lookup table" (features machine-invariant) | README | info |
| R10 | Pre-2026-10-12 p95/p99 artifacts and `calibration.json` regeneration status unknown | CANONICAL §6 | M |

## U. Unknowns
Contents of 50 PDFs; paper body; `scripts/analysis/*` logic; whether `calibration.json` was rebuilt after the warmup fix; `plan_drift` experiments (tier3/tier4) details; `site/index.html` claims; VS Code extension behavior at runtime.

## T. Traceability matrix (major items)
| Claim / feature | Code | Data/result | Test |
|---|---|---|---|
| Per-operator scaling | `operator_model.py:140` | `operator_model_metrics.json` | `tests/test_operator_model.py` (42) |
| Under-Gather parallel correction | `plan_nodes.py:153` | `parallel_speedup` axis | selftest, operator tests |
| Cross-ISA refusal | `cross_env.py:63,157` | `laptop_to_prod_metrics.json` (gha rows) | `test_product_loop.py::test_cross_arch_*` |
| Few-shot calibration (median, k≥3) | `operator_model.py:221`, `calibration_store.py` | `few_shot` arrays | `test_store_uses_a_median…`, `…provisional` |
| Redaction before leaving machine | `redact.py`, `ingest.py:131` | — | `test_ingested_plans_are_redacted…`, `test_query_text_*` |
| auto_explain ingestion | `ingest.py:105` | — | `test_auto_explain_parses_every_log_layout` |
| Telemetry never logs plans | `telemetry.py:46` | — | `test_telemetry_refuses_to_log_query_text` |
| Regression detection | `regress.py` | — | selftest identity, `test_e2e.py` |
| Gen-1 headline 21.61/23.05 | `predict.py`, `canonical_evaluation.py` | `canonical_metrics.json` | verified by re-run |
| Gate verdicts | `gate/cetp_gate.py` | `phase7_gate_evaluation.csv` (unopened) | `test_e2e.py`, CI matrix |
| BitmapAnd etc. mapped | `plan_nodes.py:50` | `version_compat.json` (unopened) | `test_bitmap_combination_nodes_have_an_axis` |
