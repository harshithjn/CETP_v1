# Datasheet — CETP cross-environment query runtime datasets

Structured after Gebru et al., *Datasheets for Datasets* (CACM 2021). TASKS.md Tier 2, item 38.

**Status: not yet published.** Publishing to Zenodo or HuggingFace needs the author's own
account and is an outward-facing act, so it is deliberately left for them. This datasheet and
`scripts/collection/package_dataset.py` are the artifacts that make publication a single step.

---

## Motivation

**Why was it created?** To answer whether a PostgreSQL query's production runtime can be
predicted from profiling on a different machine, without running it in production. No public
dataset pairs the same relational queries across multiple distinct machines with full plans
retained, which is what made the question hard to study and what makes this worth releasing
even where the models fail.

**Who created it?** Harshith J, PES University capstone UE23CS441A, 2026. Guide: Prof.
Nivedita Kasturi.

**Funding.** Self-funded; AWS spot instances, single-digit dollars.

## Composition

Six datasets. Sizes are rows, not bytes.

| File | Rows | What one row is |
|---|---:|---|
| `data/corrected/tpch_dataset_corrected_v2.csv` | 2,100 | one execution: (EC2 machine, TPC-H query, repetition) |
| `data/laptop/tpch_dataset_m1.csv` | 462 | one execution on an Apple M1 (arm64) |
| `results/elasticity_raw.csv` | 338 | one execution under a varied resource setting |
| `results/plan_drift_raw.csv` | 264 | one execution under a varied *planner belief* |
| `results/plan_features_v2.csv` | 22 | one query's 50 plan-structure features |
| `results/canonical_measurement_cv.csv` | 105 | one (machine, query) cell's timing distribution |

**Instances.** 5 AWS EC2 instance types (c5a.xlarge, m5a.xlarge, r5n.large, z1d.large,
c7i.large) plus 1 Apple M1 laptop. 21 TPC-H queries at SF1 on EC2; 22 (q15 restored) on the
M1. 20 repetitions per cell on EC2, 21 on the M1.

**Labels.** `time_ms` is PostgreSQL's own `Execution Time` from
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`. Derived targets are scaling factors
`prod_p50 / dev_p50`.

**Known defects, all documented rather than silently fixed:**

- **Repetition 0/1 of every cell is a cold-cache outlier** — the slowest of 20 in 61 of 105
  EC2 cells, by up to 8.33x. p50 is unaffected (worst case 0.7%) but **p95 is inflated >5% in
  17/105 cells and p99 in 46/105, by up to 6.92x**. Any tail statistic computed over all
  repetitions is an artifact. `is_warmup` flags it where present; the canonical pipeline
  discards it.
- **Buffer counts in the original collection were wrong**, inflated 3.06x–12.00x by summing
  PostgreSQL's cumulative per-node counters up the plan tree. Repaired in `_corrected_v2`;
  the original is quarantined in `data/raw_buggy_do_not_use/` for provenance only.
- **42 of 105 cells exceeded the protocol's own 5% CV threshold** before warmup discard; 11
  after.
- **No raw EXPLAIN plans were retained from the EC2 collection.** This is the most serious
  gap: features cannot be re-extracted, which is why the buffer bug had to be repaired from a
  local re-run rather than from the data. Fixed going forward — `scripts/collection/collect.py`
  stores full plan JSON plus a structural signature.
- **The compute hardware signature is a hardware x configuration artifact.** It was probed at
  `nproc` threads, but a query can use only `1 + max_parallel_workers_per_gather`. Invisible at
  2–4 vCPUs, catastrophic at 8 (see `results/tier1_laptop_dev.md`).
- **The STREAM-triad bandwidth probe is lost.** The bandwidth figures (8.44–14.9) cannot be
  reproduced and no new machine can be placed on that axis.
- **q16 and q20 are approximate query reconstructions** (+0.95% and −3.63% on output
  cardinality). The original query files were kept outside version control and lost; 19 of 21
  were recovered exactly. See `scripts/collection/queries/README.md`.

**Self-contained?** Yes for the measurements. The TPC-H data itself is regenerable from
gregrahn/tpch-kit at SF1; the queries are now in `scripts/collection/queries/`.

**Confidential or offensive content?** None. Synthetic benchmark data and timings only. The
IMDB data used for JOB is third-party and is *not* redistributed here.

## Collection process

**How.** `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` over psql/psycopg2; the server-reported
`Execution Time` is the measurement. EC2 instances launched as spot from a shared AMI
(`cetp-tpch-base-v1`), benchmarked, terminated. PostgreSQL at factory-default planner settings
(`shared_buffers=128MB`, `effective_cache_size=4GB`, `work_mem=4MB`,
`max_parallel_workers_per_gather=2`, `random_page_cost=4`).

**When.** EC2 collection August 2026; laptop, elasticity, plan-drift and plan-feature
collection 12 October 2026.

**Sampling.** Not a sample — the full cross product of available machines and queries. The
machines were chosen for family diversity, but **not** to decorrelate the hardware axes, which
turned out to matter: bandwidth and multi-core compute correlate at r = +0.958, making the
signature effectively one-dimensional (`results/tier1_hw_signature.md`). A future collection
should choose machines to break that correlation deliberately.

**Ethics review.** Not applicable; no human subjects.

## Preprocessing / cleaning / labelling

- Buffer counts replaced with correct root-node values per query (`results/buffer_bug_correction_report.md`).
- `rows_processed` added (`fix1_rows_processed.py`).
- Warmup repetitions flagged, and discarded by the canonical pipeline.
- Per-cell coefficient of variation computed and stored.
- **Bottleneck class labels are 21 hand-assigned judgement calls, not measurements** (Q2 and
  Q13 are acknowledged coin flips). Tier 1 found measured resource elasticity does **not**
  support them, and a uniform weighting beats a classifier trained on them. Treat them as one
  person's opinion, which is what they are.
- Raw data is retained in all cases.

## Uses

**Used for so far.** Cross-machine runtime prediction (`results/CANONICAL.md`); resource
elasticity measurement; plan-drift decomposition; conformal interval calibration; budgeted
cold-start acquisition. Headline: an analytical roofline formula reaches 21.61% MAPE against
23.05% for a bandwidth-ratio baseline (95% CI [0.32, 2.56]) under leave-one-machine-out.
Reported with it, always: scaling by `1/compute_ratio` alone scores **20.06%** — better, though
not significantly (CI [-0.41, 3.51]) — so the classifier and roofline weighting are
indistinguishable from their simplest component.

**What it should NOT be used for.**
- Any claim about hardware outside these 6 machines. Five EC2 types all on EBS; the `io`
  bottleneck class reflects buffer-touch patterns under one uniform storage backend, **not
  storage diversity**.
- Any claim about scale factors other than SF1, or about concurrent workloads — every
  measurement is single-query-at-a-time.
- **Any p95/p99 analysis using all repetitions.** Discard the warmup first.
- Estimating how *often* a new machine behaves like the c5a outlier. n=1 shows the failure mode
  exists; it cannot give a frequency.

## Distribution

**How.** Intended for Zenodo (DOI) or HuggingFace Datasets. **Not yet published.**

**Licence.** Proposed CC BY 4.0 for the measurements and MIT for the code. TPC-H and TPC-DS are
TPC specifications — derived *queries* are redistributable under the kits' terms, but check
`data/external/tpcds-kit/EULA.txt` before shipping the generated TPC-DS SQL. **The IMDB data
for JOB is third-party and must not be redistributed**; point users at
`http://event.cwi.nl/da/job/imdb.tgz` instead.

**Third-party restrictions.** See above. This is the one item that genuinely needs checking
before publication.

## Maintenance

**Who.** Harshith J.

**Will it be updated?** Yes — the Tier 2 expansion adds TPC-DS (99 queries) and JOB (113
queries), concurrency levels, and a Postgres version matrix. A machine-diversity expansion is
blocked on an AWS vCPU quota of 1.

**Errata.** Every known defect is listed under Composition above, and
`results/CANONICAL.md` is regenerated by `make results` so published numbers cannot drift from
the code.

**Versioning.** Dataset lineage is explicit:
`tpch_dataset.csv` (buggy) → `tpch_dataset_corrected.csv` → `tpch_dataset_corrected_v2.csv`
(canonical). Earlier versions are retained, quarantined and labelled.
