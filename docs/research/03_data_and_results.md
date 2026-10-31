# 03 — Data, experiments and results

## A. Datasets [OBS unless marked]
| Path | Contents | Evidence |
|---|---|---|
| `data/corrected/tpch_dataset_corrected_v2.csv` | **canonical Gen-1 dataset**: 2,100 rows = 5 EC2 machines (c5a, c7i, m5a, r5n, z1d) × 21 queries × 20 reps (Q15 excluded). Columns: machine_id, query_id, cost, time_ms, shared_hit, shared_read, rows, rows_processed | `wc -l`=2101; CANONICAL.md header |
| `data/corrected/tpch_dataset_corrected.csv` | earlier corrected version | not opened |
| `data/raw_buggy_do_not_use/` | original dataset with the buffer-summing bug (3.06–12× inflation) | its README, comments in `predict.py` |
| `data/container_grid/*.csv` | Gen-2 per-cell TPC-H runs (e.g. `tpch_c4m3g.csv`: 88 rows, ~30 columns incl. arch, pg settings, warmup flag, plan hash, plan file) + `operator_signature_grid.json`, `grid_manifest.json` | header read |
| `data/laptop/*.csv` | M1 laptop dev runs (`tpch_dataset_m1.csv` 462 rows; core-count and PG-version variants c1–c8, pg14–pg18) | header read |
| `data/external/` (imdb, job-queries, pg_hint_plan) | third-party; excluded from Docker image | directory listing |
| `models/operator_signature.json` | 13 machines: c1m1g…c8m3g, 3 `gha-ubuntu-*`, `gha-x86`, `m1-host`; `operator_rates_v2` with per-axis rates, CV, `reliable` flags | keys listed |
| `dist/cetp-datasets.tar.gz` + `MANIFEST.json` | packaged release of datasets | not opened |

Workloads: 22 TPC-H queries (`scripts/collection/queries/q1–q22.sql`), 99 TPC-DS, JOB; SF1. Schema `tpch_schema.sql`. Data generator: DuckDB `dbgen` for Gen-2 [CLAIM, workflow comment]. EC2 provenance, instance-local storage (EBS-only), collection dates (August 2026 [CLAIM, `signature.py`]) — **no raw EC2 collection logs inspected**; `scripts/collection/run_log.txt` exists, unread.
**Seeds/splits:** `random_state=42`, 2,000 bootstrap resamples (CANONICAL.md). Evaluation protocol: leave-one-machine-out (LOMO), leave-one-query-out (LOQO), both; classifier refit inside each fold ("leakage-free"); cluster bootstrap by machine.
**Known data-quality issues (CANONICAL §6):** repetition 1 of each cell is cold-cache — slowest of 20 in 61/105 cells, worst 8.33×; p50 unaffected (≤0.7 %), p95 inflated in 17/105 cells and p99 in 46/105; CV >5 % in 42/105 cells with warmup, 11/105 without.

## B. Results register
Status key: **V** = I reproduced it by running code now; **F** = figure read from a committed result file (computed by the project, not re-run by me); **C** = claim in docs/README/paper not traced.

| # | Result | Value | Source | Status |
|---|---|---|---|---|
| R1 | Gen-1 LOMO headline, analytical roofline vs naive-linear (bandwidth ratio) | **21.61 % vs 23.05 % MAPE**, +1.44 pp, 95 % cluster-bootstrap CI [0.32, 2.56] | `results/canonical_metrics.json`, `CANONICAL.md` | **V** (regenerated; `canonical_metrics.json` byte-identical to committed) |
| R2 | `cmp_only` (1/compute_ratio) | **20.06 %**; analytical−cmp_only +1.55 pp, CI [−0.41, 3.51] (not significant) | same | **V** |
| R3 | Single-core compute signal makes the model lose | analytical−naive −8.81 pp, CI [−12.13, −5.32] | `bootstrap_lomo.single_core` | **F** |
| R4 | Bottleneck classifier LOQO macro-F1 | 0.375; io recall 0; majority class "mixed" | `canonical_metrics.json` `bottleneck_classifier_loqo` | **V/F** |
| R5 | Learned model protocol sensitivity | 17.29 % seen-machine vs 26.86 % unseen (README) | README | **C** (not located in JSON by me) |
| R6 | Gen-2 8-cell docker grid, zero-shot | **superseded 2026-10-27 (C10):** operator **22.41 %** vs naive **72.52 %**; `scalar_localized_par` 22.23 % (previously 24.95 / 24.58 from a stale probe state) | regenerated `results/operator_model_metrics.json` | **V** (two byte-identical runs) |
| R6b | after regeneration only c2m1g is flagged unreliable (7 reliable cells) | reliable subset: operator 22.89 %, naive 80.63 % | `unreliable_cells`, `predictors_reliable_subset` | **F** |
| R7 | Ablation: serial-only operator / scalar_geo | 76.2 % / 76.3 % (≈ naive 72.5 %); scalar_geo_par 29.9 %; scalar_localized_par 24.6 % | same | **F** |
| R8 | Grid variation is parallelism, not serial axes | parallel_speedup spread 3.52×; serial axis spreads 1.06–1.28× | `axis_dispersion` | **F** |
| R9 | Laptop→7 same-ISA docker cells | operator **29.31 %** vs naive **37.39 %** | mean of per-machine values in `laptop_to_prod_metrics.json` (I recomputed) | **F** (arithmetic checked) |
| R9b | Per-cell: operator wins big only on c1m1g (23.5 vs 62.6) and c1m3g (31.3 vs 59.8); ties or loses on 5 of 7 | | `per_prod_machine` | **F** |
| R10 | Laptop→4 x86 GHA runners (cross-ISA) | operator **68.92 %** vs naive **21.74 %** (mean of 67.3, 66.1, 69.4, 72.8 vs 22.8, 21.8, 21.0, 21.4) | same file | **F** (arithmetic checked) |
| R11 | All 11 prod cells combined, 242 pairs | operator 43.72 % vs naive 31.70 % (**operator worse zero-shot**) | `laptop_to_prod_metrics.json` `predictors` | **F** |
| R12 | Few-shot calibration across 11 cells | k=1 spread 30.90 %, k=3 26.24 %, k=5 25.82 % (operator) | `few_shot` | **F** |
| R13 | Laptop→EC2 (Gen-1 signature, cross-ISA) | naive 129.39 %; `cmp_dop3` 160.68 %; `cmp_nproc8` 337.75 % (README cites 324 %/123 % — **different numbers**, see 06-C3); scalar-model MAPE floor 59.68 % | `laptop_to_ec2_metrics.json` | **F** |
| R14 | Cross-ISA calibrated "72.84 % → 23.38 % with 5 queries" | 72.84 % matches gha-x86 zero-shot in R10 table; 23.38 % not located | `cross_env.py` text, README | **C** (partial) |
| R15 | Online self-calibration "77.7 % → 15.0 % after 12 measurements" | | README, `addition7_summary.json` | **C** (file not opened) |
| R16 | Gate: 54.5 % of absolute BLOCK verdicts are false alarms; PASS covered 4/840 pairs | | README, `results/phase7_gate_evaluation.csv` | **C** |
| R17 | Tests | 108 collected, 108 pass (28 e2e + 42 operator + 38 product-loop) | `pytest tests -q`, 13.37 s | **V** |
| R18 | `cetp selftest` | PASSED (5 checks) | CLI run | **V** |
| R19 | Gate demo | runs; prod signature "far outside training envelope" → WARN LOW CONFIDENCE (confidence 0.00) | `gate --demo` | **V** |
| R20 | Concurrency default e = 0.734; error at 8 clients 41.8 % vs 73.8 % ignoring | | code comments | **C** |
| R21 | Cardinality term | not validated (only ratio 1.0) | `operator_model.py` docstring | stated by authors |

**Hypotheses/expected (not measured):** performance on non-TPC-H workloads, other scale factors, RDS/Aurora (the SQL probe's motivation — no managed-DB measurement found), cardinality/data-volume scaling, effect of plan forcing on real divergent plans (`plan_agreement.differing = []`, `move3_evaluable_here: false` in `operator_model_metrics.json` — the "plan comes from production" move is **untested** in the grid).
**Interpretive limits:** 8 grid cells are one host (arm64 M1) with CPU/memory quotas; serial axes barely vary (CPU quota does not slow one thread), so the grid mostly tests the parallelism term. "Naive" = assume prod = dev; its MAPE depends heavily on the pair (72 % on grid, ~21 % on x86 runners).

## C. Reproduction
`make results` → ~2 min 20 s here (138 s user). `make verify-determinism` runs it twice and `cmp`s outputs — **I did not run this target**; I ran the evaluation once and compared to committed JSON (identical), which is weaker evidence than two-run comparison but demonstrates reproduction on this machine.
Docker artifact (`make artifact-verify`) asserts analytical 21.61 / naive-linear 23.05 within 0.05 — **not run** (Docker not exercised).
