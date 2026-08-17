# Running CETP

This file lists the exact commands to run the system and explains what happens at each step. It assumes the repository is already set up as described in `README.md`.

There are three ways to run this, from fastest to most convincing. Start with the first one.

---

## 1. Gate demo on pre-built scenarios (no setup, 30 seconds)

```bash
cd gate/
python cetp_gate.py --demo
```

**What happens:** the script loads the trained bottleneck classifier and quantile regression models from `models/`, then runs four pre-built query and machine-pair combinations through the full prediction pipeline. Each one prints the predicted p50, p95, and p99 scaling factors, the confidence check result, and the final verdict.

**What you should see:** four outcomes.

- A `PASS` (query 19, `c5a` to `c7i`, SLA 200 ms) where the predicted p99 clears the threshold.
- A `BLOCK` (query 1, `r5n` to `z1d`, SLA 3000 ms) where the predicted p50 already exceeds the threshold.
- A `WARN` (query 18, `c7i` to `r5n`, SLA 8000 ms) where p50 is under the threshold but p99 is over it.
- A `WARN` for low confidence (query 19 against a hypothetical production machine far outside the training set) where the hardware-distance check flags the machine as unfamiliar and recommends a real measurement instead of trusting the prediction.

This step uses no external tools. No database, no cloud access, no network required.

---

## 2. Gate against a real, freshly run query (a few minutes, needs local PostgreSQL)

This is the step that proves the pipeline works end to end against a live database, not saved files.

**One-time setup**, if not already done:

```bash
psql -c "CREATE DATABASE tpch;"
psql tpch -f scripts/collection/dss.ddl
# load TPC-H scale factor 1 data, see scripts/collection/README for the loading steps
```

**Run one query through EXPLAIN and feed it to the gate:**

```bash
psql tpch -c "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) $(cat scripts/collection/patched_queries/q1.sql)" > /tmp/q1_explain.json

python gate/cetp_gate.py \
  --explain-json /tmp/q1_explain.json \
  --dev-machine c7i \
  --prod-machine c5a \
  --sla-ms 3000
```

**What happens:** PostgreSQL actually executes query 1 against the local database and returns a real query plan, including actual row counts and buffer page statistics. The gate parses this plan, extracts the workload cost vector, looks up the static hardware signatures already measured for `c7i` and `c5a`, computes the bandwidth and multi-core compute ratios between them, and predicts the scaling factor. It then compares the predicted p50 and p99 against the 3000 ms threshold and prints a verdict.

**What you should see:** a printed prediction (p50, p95, p99 in milliseconds), the confidence score, and PASS, BLOCK, or WARN. Nothing here is pre-computed; the plan came from a live database call made moments earlier.

---

## 3. The self-calibrating loop (2 to 3 minutes, the strongest demonstration)

This shows the system detecting an unfamiliar machine, declining to guess, learning from a measurement, and becoming accurate on that machine afterward.

```bash
python scripts/analysis/addition7_online_loop.py --demo
```

**What happens, in order:**

1. The script loads a version of the trained model with machine `c5a` entirely excluded from training, so it is genuinely unknown to the system.
2. It sends a prediction request for a query against `c5a`. The hardware-distance confidence check computes how far `c5a`'s signature is from every machine the model has seen, finds it is far outside the known range, and returns a confidence score of 0.000. The system declines to give a numeric prediction and instead recommends a real measurement.
3. The script then simulates that measurement being taken and reported back (using held-out real data as a stand-in for a fresh production measurement), and calls the store-and-retrain step. This writes the new measurement to the persistent data store, adds `c5a`'s hardware signature to the known-machines list, and retrains the classifier and regression models on the enlarged dataset.
4. The same query against `c5a` is predicted again. This time the confidence check finds `c5a` is now close to a known machine (itself, now in the training set) and returns confidence 1.000. The system produces a normal, non-deferred prediction.

**What you should see:** a before-and-after printout. Before learning, confidence is 0.000 and the error the system would have made if forced to guess is roughly 78 percent. After twelve measurements and one retrain, confidence is 1.000 and the error on held-out queries against `c5a` drops to roughly 15 percent.

This demonstrates the mechanism working correctly, using held-out data in place of a genuinely fresh production measurement. It has not been tested against a continuously running live production system, and that distinction should be stated plainly if asked.

---

## 4. Continuous integration gate (shows the deployment integration)

```bash
cd gate/
git commit -am "example change"
git push
```

**What happens:** pushing triggers `.github/workflows/cetp-gate.yml`, which runs the gate automatically across a small matrix of example queries declared in `cetp.yml`, using the fixed development and production machine pair configured there. Each query in the matrix produces a verdict; the workflow fails the build if any query returns BLOCK.

**What you should see:** on GitHub, under the Actions tab, a running workflow that completes with each query's verdict shown in the job summary, and a red or green build status depending on whether any query blocked.

---

## Important distinction when demonstrating this

The five cloud machines used to collect the training data (`c5a`, `m5a`, `r5n`, `z1d`, `c7i`) are terminated. Nothing in the demonstrations above runs on live EC2 instances. What is running live is a local PostgreSQL database and the trained models, which were built from data collected on those cloud machines during the project. If asked directly whether the cloud machines are live during a demo, the honest answer is no: cloud collection was a one-time data-gathering phase, now complete, and everything shown afterward runs locally against the resulting models.

---

## Running the test suite

```bash
cd tests/
python -m pytest test_e2e.py -v
```

**What happens:** the full integration test suite runs, covering the complete chain from raw EXPLAIN input to verdict, a leakage check confirming no production-side data reaches the model as a feature, malformed-input handling, and a known-answer regression check against locked expected predictions.

**What you should see:** 28 tests, all passing.

---

## Where results come from

Every number shown by the commands above, and every number in the paper and dashboard, is generated by scripts in `scripts/analysis/`, reading from `data/corrected/`. None of it is hand-entered. To regenerate any reported figure from scratch, the relevant script in `scripts/analysis/` can be run directly; each corresponds to one phase or addition described in the paper.
