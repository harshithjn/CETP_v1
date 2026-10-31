# 08 — Per-operator vs roofline: what is implemented, what is measured, what is the contribution

Added 2026-10-27 after reading the previously unread material. Labels as in 00: **[OBS]** read/run by me, **[CLAIM]** in docs, **[DED]** my deduction.

## 0. Additional sources now inspected
- **All 50 deleted Markdown documents** recovered with `git show 38c4bf2^:<path>` and read where relevant (OPERATOR_MODEL, IMPLEMENTATION_REFERENCE, TASKS, results/operator_model, laptop_to_prod, cross_isa_prediction, cross_isa_operator_rates). **The PDFs themselves were not opened** — no PDF text extractor is installed (`pdftotext` missing). `docs/INDEX.md` says each PDF is rendered from these Markdown files; I did not verify that equivalence. If you need that verified, tell me or give me PDF text.
- `scripts/analysis/operator_model_evaluation.py` (first 260 of 491 lines) and re-executed it; `canonical_evaluation.py` structure (function list, predictor definitions); `scripts/collection/{predict_prod,container_grid,operator_probe}.py` function lists only (bodies still unread); all VS Code extension `src` files except the last half of `sidebarProvider.ts`; `gate/cetp_predict_cli.py` head; `cetp-regression-check.yml`; test names in `test_e2e.py`, `test_operator_model.py` (first 200 lines), `test_product_loop.py`; `docs/paper/CETP_paper.tex` contributions and tables.
- **Still not read:** `hw_probe_suite.c`, `hw_signature.py`, `laptop_to_prod_evaluation.py`, `laptop_to_ec2_evaluation.py`, bodies of `container_grid.py` / `predict_prod.py` / `operator_probe.py`, the remaining ~25 analysis scripts, `data/plans_grid/` (git-ignored, local only), the paper body past the contributions list.

## 1. The two methods, side by side

| | "Roofline" (Gen 1) | "Per-operator" (Gen 2) |
|---|---|---|
| Implemented? | **Yes.** `cetp/predict.py:analytical_scaling` (l.67), `compute_only_scaling` (l.82), `hardware_distance_confidence`; `cetp/regress.py:detect`; `cetp/signature.py`; legacy `gate/cetp_gate.py` (RF classifier + GBR quantiles, pickles). Caveat: it is a *roofline-style weighted blend* `0.5/bw + 0.25/cmp + 0.25·√(1/bw·1/cmp)`, not a max-of-bounds roofline [OBS]. | **Yes.** `cetp/plan_nodes.py:decompose`, `cetp/operator_model.py:predict_scaling / weights_from_plan / fit_calibration / select_calibration_queries`, `cetp/cross_env.py:predict`, `calibration_store.py`, `ingest.py`, `telemetry.py`; probe `scripts/collection/operator_probe.py`; CLI `cetp predict-prod`. |
| Inputs | dev EXPLAIN (root cost, buffers, time) + 2 scalars per machine (STREAM bandwidth, multi-core GFLOPS) | dev EXPLAIN ANALYZE (per-node times) + 5 SQL operator rates + `parallel_speedup` per machine; optional prod plan, calibration factor, client count |
| Outputs | point estimate + conformal p90/p95/p99 multipliers (×1.366/1.472/1.967 [CLAIM]), confidence, REGRESSION/NEUTRAL/IMPROVEMENT, gate PASS/WARN/BLOCK | scaling factor, predicted ms, per-axis ratios, `explained_frac`, `trust` LOW/MEDIUM/HIGH, warnings; **no intervals** |
| Key calculations | ratio of bandwidth and compute; `1/compute_ratio`; hardware-distance confidence | self-time decomposition; wall-time weights; `rate_dev·s_dev/(rate_prod·s_prod)` on nodes under a Gather; geomean fallback for unmapped/unpriced; median few-shot correction |
| Used by shipped product surfaces | **VS Code extension** (`runner.ts` → `gate/cetp_predict_cli.py` with bandwidth/compute args, seeded with the 5 EC2 legacy profiles), **regression-check workflow** (`cetp regress`), **gate workflow**, `cetp predict`, `selftest` | **Only** `cetp predict-prod`, plus the calibration/ingest/telemetry modules that feed it. Not wired into the extension or either CI workflow [OBS: grep for cross_env/operator_model usage] |
| Tests | `test_e2e.py` (28: chain, known answers, verdict branches, leakage guard, malformed input, config, confidence boundary, buffer accounting). Known-answer values were re-derived after retraining (PROVENANCE) | `test_operator_model.py` (42) + `test_product_loop.py` (38): unit/behavioural tests of decomposition, under-Gather, weights, calibration, refusal, ingest, telemetry. **They verify mechanics, not accuracy.** No test asserts any MAPE. |
| Canonical evidence | `canonical_metrics.json` **reproduced by me byte-identically**: analytical 21.61 %, naive-linear (bandwidth) 23.05 %, `cmp_only` 20.06 %; classifier macro-F1 0.375; single-core variant loses by 8.8 pp | `operator_model_metrics.json` etc. — **not reproducible as committed** (see §3) |
| Status | Primary *implemented product* surface; **research result is mostly negative** | Primary *research* implementation of the stated thesis (laptop→prod); **experimental/alpha**; cardinality term unvalidated; plan-forcing (move 3) untestable on data collected |

## 2. What each method's evidence actually establishes

**Roofline / Gen 1 (5 EC2 machines × 21 queries, SF1, all x86 EBS):**
1. Using multi-core compute rather than bandwidth as the scaling signal beats the bandwidth ratio: +1.44 pp, CI [0.32, 2.56] (V). Cluster = 5 machines.
2. The roofline blend and the bottleneck classifier add **nothing** over `1/compute_ratio` (+1.55 pp, CI [−0.41, 3.51]) or over uniform weights (21.55 % vs 21.61 %). Classifier macro-F1 0.375, io recall 0, and its 6 features are machine-invariant (a query-id lookup).
3. Single-core compute gives the wrong sign and loses to naive by 8.8 pp (CI excludes 0).
4. The learned GBR interpolates, does not extrapolate (17.29 % seen vs 26.86 % unseen [CLAIM]).
5. It **fails on the real task**: arm64 laptop → EC2 is 160.7–337.7 % vs naive 129.4 % (`laptop_to_ec2_metrics.json`).
6. Absolute-SLA gating: 54.5 % false-alarm BLOCKs [CLAIM]; the "releasable" mode is therefore *relative* (`regress`), whose justification is arithmetic (hardware term cancels), not a measured detection rate — **no regression-detection accuracy experiment was found**.

**Per-operator / Gen 2 (committed JSON: 8 arm64 docker cells, 22 queries, 1232 pairs; plus laptop→11 cells):**
1. Capability signature **without** a parallelism term ≈ naive or worse (71–76 % vs 72.5 %) — a strong, mechanistically explained result (serial axes span 1.02–1.13× because a CPU quota does not slow one thread; parallel speedup spans 3.5×).
2. Adding `parallel_speedup` applied only below Gather: 24.95 % (reliable 4 cells: 22.23 % vs naive 74.99 %). Localising beats whole-query application by ~6.9 pp (CI excludes 0 per OPERATOR_MODEL.md [CLAIM]).
3. **Per-operator rate weighting adds nothing**: `scalar_localized_par` 24.58 % vs `operator` 24.95 % (committed JSON); −0.08 pp CI [−0.73, +0.44] in the Markdown. Cross-ISA test also refuted the stated motivation: operator-mix spread 1.365× across ISA vs 1.371× within the arm64 grid.
4. Laptop → same-ISA cells: 29.31 % vs naive 37.39 % (7 cells); gains only on the two 1-CPU cells; ties/loses on 5 of 7.
5. Laptop → x86 (4 GHA runners): **68.92 % vs 21.74 %**. Calibrated with 5 queries: ~23.4 % vs naive 21.4 % — **parity, not improvement**.
6. All 11 targets: 43.72 % vs 31.70 % — zero-shot loses to naive overall.
7. Few-shot: k=1 can be worse than none; k≥3–5 needed. Cold-start study (Gen-1 data) 15.84 % → ~13 %.
8. Untested: plan forcing (0/22 plans differed in committed grid), cardinality term, I/O regime (diagnosed residual, Spearman 0.302), concurrency (default exponent; 41.8 % residual at 8 clients), managed Postgres, non-TPC-H.

## 3. New evidence-integrity findings (all [OBS])
| ID | Finding | Severity |
|---|---|---|
| C10 | **RESOLVED 2026-10-27 — see `09_C10_investigation_and_fix.md`.** Two defects: the script globbed `data/container_grid/` (silently adding 4 x86 cells → 12 cells/2,904 pairs), and the committed outputs came from an older probe state (`models/operator_signature.json` @26649a1) than the signature file committed with them. Fixed by selecting cells from the manifest and recording input hashes; results regenerated from the authoritative inputs (operator 22.41 %, scalar_localized_par 22.23 %, diff −0.17 pp CI [−0.49, +0.10], ns). Numbers quoted in §2/§5 of this file marked 24.95/24.58 are the *superseded* committed values. | ~~High~~ Resolved |
| C11 | (Explained by C10 defect A.) The re-run mixed ISAs inside one "grid", so its absolute numbers are confounded and should not be quoted either; it shows the script was not re-pinned to the 8-cell grid when x86 cells were added. | High |
| C12 | Three different states of the same experiment are in circulation: Markdown/PDF says operator **22.30 %**, 2 unreliable cells, PC1 59.4 %/rank 4; committed JSON says **24.95 %**, 4 unreliable cells; paper uses 24.95 %. `scalar_localized_par − operator` is −0.08 pp (md) vs −0.37 pp (JSON). | Med |
| C13 | `data/plans_grid/` (per-plan JSONs the evaluation requires) is git-ignored: a fresh clone cannot run the per-operator evaluation at all; the Docker artifact covers only Gen 1. | Med |
| C14 | The paper (`docs/paper/CETP_paper.tex`, untracked) already frames the work as probe + parallel-term model + cross-ISA failure analysis + calibration/refusal + three negative results — consistent with this analysis — but its 24.95 % figure rests on the not-reproducible state in C10. | Med |
| C15 | The VS Code extension and CI regression workflow expose Gen 1 only; the README banner says Gen 1 "is being replaced", yet it is the only generation reachable from the product UI. | Med |

## 4. Classification of each capability
| Capability | Class |
|---|---|
| `compute_only_scaling` (1/multi-core compute ratio) | primary implemented *baseline*; best Gen-1 estimator; supporting evidence only |
| Roofline blend + RF classifier + GBR gate | experimental/legacy; shown not to add value |
| Conformal intervals (`calibration.json`) | supporting technique; marginal coverage only; regeneration after warmup fix unverified |
| `regress` (before/after ratio) | implemented product feature; **unvalidated empirically** (arithmetic argument only) |
| SQL operator probe | primary implemented measurement capability; valid within an ISA; absolute scale not transferable |
| `parallel_speedup` + under-Gather localisation | **primary supported technical finding** |
| Per-operator rate weighting | implemented, **experimental, null result** |
| Cardinality term, plan forcing (`predict_prod.py`) | experimental/unvalidated |
| Concurrency factor | experimental, population default |
| Calibration store + cross-ISA refusal | implemented, mechanically tested; effect measured on one x86 family |
| ingest/telemetry/redaction | supporting product infrastructure, unit-tested, not field-tested |

## 5. Claims justified vs overstated
**Justified:** (a) a capability signature must model parallelism, and applying it only below Gather is what makes a probe-based predictor beat "assume prod = dev" within one ISA and host; (b) neither roofline weighting nor per-operator rate weighting is distinguishable from its simplest component on the available data; (c) absolute probe scale does not transfer across microarchitectures (4/4 replication, two AMD EPYC models), so cross-ISA needs calibration and even then only reaches parity with naive; (d) probe validity requires an idle machine; (e) the Gen-1 headline reproduces (21.61 vs 23.05) and is modest; (f) the original two-scalar signature is effectively one-dimensional (r=0.958).
**Overstated if said:** "CETP predicts production runtime accurately" (zero-shot 43.7 % vs 31.7 % naive over 11 targets; cross-ISA 3× worse than doing nothing); "per-operator modelling" or "roofline modelling" is the contribution; "works on RDS/Aurora" (never measured); "regression detection is reliable" (not tested); "the model beats naive on x86" (parity at best); "24.95 % vs 72.52 %" without the reliable-subset and per-cell caveats and without the reproducibility caveat C10; "98 % …/conformal coverage guarantees" (marginal only; 90.5 % worst machine).

## 6. Recommendation → see final answer in chat. Short form: **Option C, tightly defined** — the two analyses are *not* two complementary methods; they are two generations of one idea that converge on the same finding. State the contribution as the parallelism-aware capability-scaling finding, the cross-ISA failure analysis with calibration/refusal, and the negative results that retire both the roofline and the per-operator decompositions. Do not present either named method as the primary contribution.

## 7. Remaining gaps (priority)
1. Resolve C10: decide which cell set defines the "container grid", pin the evaluation to it, regenerate JSON/CSV/Markdown together, and force-add or document `data/plans_grid`.
2. A regression-detection accuracy experiment (labelled before/after plan pairs) — or drop the claim.
3. Read bodies of `operator_probe.py`, `container_grid.py`, `predict_prod.py`, `hw_probe_suite.c`, `laptop_to_*_evaluation.py`.
4. Verify PDFs equal the recovered Markdown (needs a PDF text extractor or your export).
5. Provide EC2 provenance/raw logs and the origin of 324 %/123 % (the JSON gives 337.75 %/129.39 %; OPERATOR_MODEL.md cites 324.24 %/123.36 % from `tier1_laptop_dev`, a *different* experiment from `laptop_to_ec2_metrics.json` — likely reconciles, not yet confirmed).

## 8. Contribution assessment after the C10 fix (2026-10-27)
Option C, narrowly defined, is **unchanged**. The corrected per-operator numbers reproduce deterministically and move by ≤2.6 pp; every qualitative finding survives: no-parallelism signatures ≈ naive (67–71 % vs 72.5 %); localising the parallel term is significant (+7.9 pp, CI [1.45, 16.31]); per-operator rate weighting indistinguishable from one scalar (−0.17 pp, CI spans 0). Two statements must be softened: the "4-dimensional signature" claim (now rank 3, PC1 72.4 %, mean|r| 0.65) and any figure quoted to better than ±2–3 pp (probe re-run shifts results by that much). Neither Roofline nor per-operator analysis becomes a primary contribution; the cross-ISA failure (68.9 % vs 21.7 %) and the calibration-mandatory conclusion are untouched by this fix because they come from `laptop_to_prod_*`, which was not regenerated.
