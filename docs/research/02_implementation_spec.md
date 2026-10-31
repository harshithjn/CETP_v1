# 02 — Implementation specification (code-verified)

All line numbers refer to the working tree on 2026-10-27.

## A. Plan decomposition — `cetp/plan_nodes.py:98` `decompose()`
**Problem:** PostgreSQL `Actual Total Time` is cumulative and per-loop averaged; naive summing is wrong.
**Steps:** (1) `inclusive_ms = Actual Total Time × Actual Loops` (l.113-118). (2) `self_ms = inclusive − Σ child inclusive`, clamped at 0 (l.131-134). (3) `operator_for()` maps node type → probe axis via `OPERATOR_MAP` (Seq Scan→seqscan; Hash/Hash Join→hashjoin; Sort/Merge Join→sort; Index/Bitmap scans, Nested Loop→nestloop), `Aggregate` by Strategy (Hashed/Mixed→hashagg, Sorted→sort, Plain→seqscan); `PASSTHROUGH` set (Gather, Limit, Materialize, …) → `None` (unmapped). (4) `under_gather` is propagated to every descendant of a Gather/Gather Merge (l.153-157) — the comment explains keying on `Parallel Aware` corrected only 46 % of a q19 plan. (5) returns `residual_frac = (Execution Time − Σ self)/Execution Time`.
**Failure cases:** parallel plans: self-times are CPU-averaged, not wall (docstring); missing `Actual Total Time` → `self_ms=None`, `analyzed=False`; unknown node types → unmapped.
**Example (from code comment, [CLAIM]):** parallel q19 self-times sum to 2.85× wall.

## B. Per-operator scaling — `cetp/operator_model.py`
Formula (docstring l.1-14): `scaling = Σ_op w_op · (rate_dev·sd)/(rate_prod·sp) · card_op`, `T_prod = T_dev · scaling`.
- `weights_from_plan` (l.89): bucket self-time by `(operator, under_gather)`; parallel buckets divided by `dev_speedup` (CPU-share → wall-share); normalise to sum 1; unmapped share returned.
- `predict_scaling` (l.140): ratio uses `dev_speedup`/`prod_speedup` only on parallel buckets; unmapped and unpriced axes are scaled by the **geometric mean** of mapped ratios (so weights still sum to 1); multiplies by `calibration` and `concurrency_factor`.
- `cardinality_factor` (l.68): linear in row ratio; sort: `r·(1+ln r/ln n)` with `DEFAULT_SORT_ROWS=1e5`. **Unvalidated** (only exercised at ratio 1.0 per docstring).
- `concurrency_factor` (l.132): `N^e`, default `e = 0.734` (`DEFAULT_CONCURRENCY_ELASTICITY`), "median across 22 TPC-H queries on an 8-core machine" [CLAIM].
- `fit_calibration` (l.221): **median** of `actual/predicted`. `select_calibration_queries` (l.239): `spread` rule — k queries evenly spread over predicted-scaling range; k=1 → median item.
**Assumptions:** probe rates comparable (same `probe_scale`); weights from dev time carry to prod; unmapped work scales like the mapped average. **Complexity:** O(nodes) for prediction; selection O(k·n).
**Toy example (cli selftest, `cli.py:267-284`):** Gather(100 ms)→Parallel Seq Scan(90 ms, loops=3). Identical rates and speedups 2.5/2.5 → scaling = 1.0 exactly; prod speedup 1.0 → scaling 2.50 (> 2.0 asserted). I reproduced: "prod without parallelism 2.50x slower".

## C. Product flow — `cetp/cross_env.py`
`predict()` (l.141): (1) if dev/prod `client_platform` last token differ (e.g. `arm64` vs `x86_64`) **and** `calibration == 1.0` **and** not overridden → raises `CrossArchitectureUncalibrated` (l.157-165). (2) optional `row_ratio_from_plans` (prod plan rows / dev actual rows). (3) `predict_runtime`. (4) `_trust()`: cross-arch → LOW/MEDIUM; explained<0.5 → LOW; forcing failed → LOW; uncalibrated → MEDIUM cap; else HIGH unless unmapped>0.25. (5) `_warnings()` text embeds measured numbers (72.84 vs 21.41; 28.54 vs 38.22; 33.74 …) — see 06 for provenance of these.
`load_rates()` (l.84) rejects `operator_rates_v1`, probes with `invalid_probes`, and `reliable is False` records.

## D. Gen-1 predictor — `cetp/predict.py`
`features_from_plan` (root-node buffers only; summing the tree inflated the old dataset 3.06–12×). `analytical_scaling` default weights `{bandwidth:.5, compute:.25, mixed:.25}`; `compute_only_scaling = 1/compute_ratio` (`predictor="cmp_only"`). Interval: `point × cal["predictors"][p]["multiplier"][level]` from `models/calibration.json` (conformal quantiles of log(actual/pred), LOMO). `hardware_distance_confidence = 1 − nearest normalised distance/√2` over the 5 LEGACY machines; threshold 0.6 hand-set. Note `predict()` always uses LEGACY machines as the reference set regardless of which signature kind is passed (l.131).
`signature.ratios()` refuses to mix `legacy_frozen` and `registry_probe_v2` signatures.

## E. Regression detection — `cetp/regress.py:30`
`prod_ratio = (dev_after·s_after)/(dev_before·s_before)`; `hardware_term = s_after/s_before`; "cancelled" if |1−hw|<0.02; verdict REGRESSION if ratio ≥ 1.20, IMPROVEMENT if ≤ 1/1.20. CLI `--fail-on-regression` returns exit 1. Selftest asserts identity (same plan twice → 1.000).

## F. Calibration, ingest, telemetry
- `CalibrationStore` (key `"dev->prod"`, observations keyed by query id so re-measure supersedes; `MIN_OBSERVATIONS=3` → `provisional`; atomic tempfile+`os.replace`; corrupt file → zero-shot).
- `ingest.parse_auto_explain_log`: finds `duration: X ms plan:`, balanced-brace JSON extraction, strips repeated log prefixes, **always** redacts + `assert_clean` (strict raises; non-strict drops). `rank_queries` ranks by total time; top 3 flagged `calibration_candidate`.
- `Telemetry`: JSONL; `_FORBIDDEN` keys (`query_text`, `plan`, …) rejected; `resolve` attaches actual to most recent unresolved match; `summary` reports MAPE **and** naive MAPE.

## G. Redaction — `cetp/redact.py`
Regex replace of string literals → `'?'`, numeric literals → `?` in `EXPRESSION_FIELDS` and `QUERY_TEXT_FIELDS`; at `level="full"` identifiers → salted blake2s (5-byte) tokens and query text → `[redacted]`. `assert_clean` re-scans. **Limits [DED]:** regex-based; default salt is the public string `"cetp"` (hash tokens are guessable by dictionary unless caller passes `--salt`); `structural` keeps relation/column names.

## H. Legacy gate — `gate/cetp_gate.py`
Loads 3 pickles + `scaling_feature_columns.json`; RF classifier → class probabilities → `analytical_scaling` (io folded into bandwidth) → GBR quantile models predict p50/p95/p99 scaling → `three_state_verdict`: low confidence → WARN; p50>SLA → BLOCK; p99≤SLA → PASS; else WARN. Exit code 1 on BLOCK. Config `gate/cetp.yml` (SLA default 500 ms, q1 3000, q18 8000 …).

## I. Measurement code (not read in depth)
`operator_probe.py` header [OBS]: probes run in SQL with parallelism off and JIT off, `work_mem` pinned, rates are *instrumented* tuples/s (EXPLAIN ANALYZE overhead), valid only as ratios. `container_grid.py` builds 4 CPU × 2 memory docker cells (c{1,2,4,8}m{1,3}g). Bodies of these and `predict_prod.py` (pg_hint_plan forcing) **not yet audited**.
