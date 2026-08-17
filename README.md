# Cross-Environment TPC-H Runtime Prediction (CETP)

CETP predicts how long a TPC-H analytical query will take on a production machine, using only a query plan captured on a development machine and static hardware specifications for both machines. The problem it addresses is that query performance does not transfer across hardware in a simple way: a query that is fast in development can be slow in production, or vice versa, and running a full benchmark suite on every candidate production machine before every deployment is expensive. CETP replaces that benchmark with a prediction, so a CI/CD pipeline can flag a likely SLA violation before a query ever reaches production.

## How it works

CETP decomposes the prediction into two independent parts. The first is the workload cost, extracted from a PostgreSQL `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plan on the development machine: planner cost, shared buffer hits and reads, and row counts. The second is the hardware signature, a pair of static scores per machine, memory bandwidth and multi-core compute throughput, measured once via short microbenchmarks. A Random Forest classifier uses the workload cost to estimate whether a query is bandwidth-bound, compute-bound, I/O-bound, or mixed, and a set of gradient-boosted quantile regressors combine that classification with the ratio of the two machines' hardware signatures to predict the production runtime's p50, p95, and p99. A hardware-distance check flags predictions made for a production machine that lies far outside the training set, so the system can decline to guess rather than answer confidently on unfamiliar hardware.

## Headline result

Across 5 EC2 instance types and 21 TPC-H queries, a single bandwidth ratio is a strong baseline that no more complex approach beat by a statistically significant margin, with one exception: once the compute microbenchmark was corrected to measure multi-core throughput instead of single-core throughput, the physics-based formula using it significantly outperformed the baseline (21.60% MAPE vs. 23.03%, 95% CI on the improvement excludes zero). Full evidence is in `results/addition4_compute_benchmark_report.md` and `results/phase7_report.md`.

## Repository structure

```
cetp/
  README.md              this file
  gate/                  cetp_gate.py (SLA gate CLI), cetp_gate_demo.py, cetp.yml, example queries
  dashboard/              static research dashboard (index.html, data.js, plot assets)
  models/                 final trained model artifacts (classifier, quantile regressors, hardware signatures)
  models_online/          versioned self-calibration snapshots from the online learning addition
  data/
    raw/                  original TPC-H measurement dataset, buffer counts inflated by a since-fixed bug
    corrected/             buffer-bug-corrected dataset and its row-processed follow-up, used by current scripts
  scripts/
    collection/            TPC-H collection harness, patched queries, compute/bandwidth microbenchmarks
    analysis/               phase and addition scripts that produced every number in results/
  tests/                  test_e2e.py and its EXPLAIN-plan fixtures
  results/                research reports, evaluation metrics, and figures, organized by phase and addition
  .github/workflows/     CI gate that runs cetp_gate.py against example queries on every push
```

## Running the gate demo

From the repository root:

```bash
python gate/cetp_gate.py --demo
```

This runs four worked scenarios covering every verdict path (PASS, BLOCK, WARN, and WARN for low confidence) and writes a report to `results/phase8_cetp_gate_demo.md`. To run the gate against a single query directly:

```bash
python gate/cetp_gate.py --explain-json path/to/explain.json --config gate/cetp.yml --query-id q1
```

To see the online self-calibration loop, which detects an unfamiliar machine, learns from 12 measurements, and cuts its error on that machine from 77.7% to 15.0%:

```bash
python scripts/analysis/addition7_online_loop.py
```

To view the research dashboard:

```bash
python3 -m http.server 8000 --directory dashboard
```

Then open `http://localhost:8000`.

To run the test suite:

```bash
pytest tests/test_e2e.py
```

## Further detail

The full writeup, including the complete experimental methodology, every addition and fix made after the initial results, and the paper itself, is maintained outside this repository. Within this repository, `results/phase7_report.md` is the most complete single account of the final method and its evaluation, and each other file in `results/` documents one phase or addition referenced from there.

## Limitations

The compute-side hardware signal is only usable in its corrected multi-core form; the single-core version made predictions worse. The learned quantile model shows no significant edge over the bandwidth-ratio baseline and is not competitive as a standalone p99 gate. See `results/phase7_report.md` (Limitations section) for the complete discussion, including the buffer-counting bug and its correction.

## Status

Academic capstone project.
