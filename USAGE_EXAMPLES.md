# CETP v1 - Practical Usage Examples

## Table of Contents
1. [Quick Demo](#1-quick-demo)
2. [Single Query Prediction](#2-single-query-prediction)
3. [Using Configuration Files](#3-using-configuration-files)
4. [CI/CD Integration](#4-cicd-integration)
5. [Online Learning Demo](#5-online-learning-demo)
6. [Understanding the Output](#6-understanding-the-output)

---

## 1. Quick Demo

**Fastest way to see CETP in action:**

```bash
cd /home/kartik/Desktop/capstone/CETP_v1
venv/bin/python gate/cetp_gate.py --demo
```

**What you'll see:**
- 4 scenarios covering all verdict types
- PASS, BLOCK, WARN, and low-confidence examples
- Takes ~30 seconds
- Report saved to `results/phase8_cetp_gate_demo.md`

---

## 2. Single Query Prediction

### Option A: Using Pre-extracted Features

If you already have query features extracted:

```bash
venv/bin/python gate/cetp_gate.py \
  --dev-features gate/examples/gate_queries/q19_c5a_dev_features.json \
  --dev-bandwidth 14.9 \
  --dev-compute 0.593 \
  --prod-bandwidth 8.44 \
  --prod-compute 0.807 \
  --sla-ms 200
```

**Output:**
```
CETP Gate verdict for query: PASS
  dev time: 43.7 ms  |  bandwidth_ratio=0.566  compute_ratio=1.361
  bottleneck probs: bandwidth=0.99, compute=0.00, io=0.01, mixed=0.00
  predicted prod runtime: p50=59.1 ms  p95=79.9 ms  p99=129.6 ms
  hw-distance confidence: 1.00 (threshold 0.6)
  reason: predicted p99 (129.6 ms) clears SLA (200.0 ms).
```

### Option B: Using EXPLAIN JSON (from PostgreSQL)

If you have a PostgreSQL EXPLAIN output:

```bash
venv/bin/python gate/cetp_gate.py \
  --explain-json gate/examples/gate_queries/q19_c5a_explain.json \
  --dev-bandwidth 14.9 \
  --dev-compute 0.593 \
  --prod-bandwidth 8.44 \
  --prod-compute 0.807 \
  --sla-ms 200
```

---

## 3. Using Configuration Files

### Create/Edit a Config File

Example config (`gate/cetp.yml`):

```yaml
dev_hardware:
  machine_id: c7i
  bandwidth_gbs: 8.44
  compute_score: 0.807

prod_hardware:
  machine_id: c5a
  bandwidth_gbs: 14.9
  compute_score: 0.593

sla:
  default_ms: 500
  per_query:
    q1: 3000
    q18: 8000
    q19: 200

confidence:
  low_confidence_threshold: 0.6
```

### Use the Config File

```bash
venv/bin/python gate/cetp_gate.py \
  --dev-features gate/examples/gate_queries/q19_c7i_dev_features.json \
  --config gate/cetp.yml \
  --query-id q19
```

**Benefits:**
- Store hardware specs once, use for all queries
- Per-query SLA thresholds
- Easier CI/CD integration
- Override with command-line args if needed

---

## 4. CI/CD Integration

### Get Machine-Readable JSON Output

```bash
venv/bin/python gate/cetp_gate.py \
  --dev-features gate/examples/gate_queries/q19_c7i_dev_features.json \
  --config gate/cetp.yml \
  --query-id q19 \
  --json
```

**JSON Output Structure:**
```json
{
  "query_id": "q19",
  "dev_features": { ... },
  "dev_signature": { "bandwidth": 8.44, "compute": 0.807 },
  "prod_signature": { "bandwidth": 14.9, "compute": 0.593 },
  "bandwidth_ratio": 1.765,
  "compute_ratio": 0.735,
  "bottleneck_probabilities": { ... },
  "predicted_scaling_factor": {
    "p50": 0.734,
    "p95": 1.024,
    "p99": 1.968
  },
  "verdict": {
    "state": "PASS",
    "reason": "predicted p99 (104.6 ms) clears SLA (200.0 ms)",
    "predicted_prod_ms": {
      "p50": 39.0,
      "p95": 54.4,
      "p99": 104.6
    },
    "confidence": 1.0,
    "low_confidence": false,
    "sla_ms": 200.0
  }
}
```

### Exit Codes

- `0` = PASS or WARN (safe to proceed)
- `1` = BLOCK (deployment should fail)

### Example CI/CD Script

```bash
#!/bin/bash

# Run CETP gate for query
venv/bin/python gate/cetp_gate.py \
  --explain-json "$EXPLAIN_PATH" \
  --config gate/cetp.yml \
  --query-id "$QUERY_ID" \
  --json > cetp_result.json

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ CETP Gate: PASS or WARN - deployment allowed"
    exit 0
else
    echo "🛑 CETP Gate: BLOCK - predicted SLA violation"
    cat cetp_result.json
    exit 1
fi
```

---

## 5. Online Learning Demo

**Watch the system learn from unfamiliar hardware:**

```bash
venv/bin/python scripts/analysis/addition7_online_loop.py --demo
```

**What it demonstrates:**

1. **Before Learning:**
   - System sees unknown machine (c5a)
   - Confidence = 0.000 (refuses to predict)
   - If forced to predict: 77.7% error

2. **Learning Phase:**
   - System receives 12 real measurements
   - Retrains models with new data
   - Registers c5a as known machine

3. **After Learning:**
   - Confidence = 1.000 (high confidence)
   - Error reduced to 16.1%
   - Improvement: 61.6 percentage points

**Output:**
```
BEFORE learning: MAPE=77.72%  confidence=0.000
AFTER learning:  MAPE=16.12%  confidence=1.000
Improvement:     +61.60 pp
```

---

## 6. Understanding the Output

### Verdict States

| State | Meaning | Exit Code | Action |
|-------|---------|-----------|--------|
| **PASS** | p99 < SLA threshold | 0 | ✅ Safe to deploy |
| **BLOCK** | p50 > SLA threshold | 1 | 🛑 Block deployment |
| **WARN** | p50 < SLA < p99 | 0 | ⚠️ Risky, consider canary |
| **WARN (low confidence)** | Unknown hardware | 0 | ⚠️ Run real measurement |

### Key Metrics Explained

**Hardware Ratios:**
```
bandwidth_ratio = prod_bandwidth / dev_bandwidth
compute_ratio = prod_compute / dev_compute
```
- `> 1.0` means production is faster
- `< 1.0` means production is slower

**Bottleneck Probabilities:**
- Shows what limits query performance
- `bandwidth=0.99` = query is 99% bandwidth-bound
- `compute=0.99` = query is 99% compute-bound
- `mixed=0.99` = query uses both resources
- `io=0.99` = query is I/O-bound

**Confidence Score:**
- `1.00` = High confidence (hardware is known)
- `< 0.60` = Low confidence (hardware is unfamiliar)
- Based on distance from training set

**Predicted Runtimes:**
- `p50` = median (50th percentile)
- `p95` = 95th percentile
- `p99` = 99th percentile (worst case)

### Example Interpretation

```
CETP Gate verdict for q19: PASS
  dev time: 43.7 ms
  bandwidth_ratio=0.566  compute_ratio=1.361
  bottleneck probs: bandwidth=0.99, compute=0.00, io=0.01, mixed=0.00
  predicted prod runtime: p50=59.1 ms  p95=79.9 ms  p99=129.6 ms
  confidence: 1.00
  reason: predicted p99 (129.6 ms) clears SLA (200.0 ms)
```

**What this means:**
1. Query took 43.7 ms on dev machine
2. Production has 0.566x bandwidth (slower) but 1.361x compute (faster)
3. Query is bandwidth-bound (99% probability)
4. Predicted prod runtime: 59-130 ms range
5. Even worst case (p99=130ms) is under SLA (200ms)
6. **Verdict: SAFE TO DEPLOY** ✅

---

## 7. Available Example Queries

Located in `gate/examples/gate_queries/`:

```
q1_c7i_dev_features.json   # Query 1 on c7i
q1_r5n_dev_features.json   # Query 1 on r5n
q18_c7i_dev_features.json  # Query 18 on c7i
q19_c5a_dev_features.json  # Query 19 on c5a
q19_c5a_explain.json       # Query 19 full EXPLAIN output
q19_c7i_dev_features.json  # Query 19 on c7i
```

Try different combinations to see various verdicts!

---

## 8. Hardware Signatures Reference

Pre-measured EC2 instances in training set:

| Machine | Bandwidth (GB/s) | Compute Score | Notes |
|---------|------------------|---------------|-------|
| c5a | 14.9 | 0.593 | High bandwidth |
| c7i | 8.44 | 0.807 | High compute |
| m5a | 9.78 | 0.636 | Balanced |
| r5n | 13.3 | 0.605 | Memory-optimized |
| z1d | 13.8 | 0.482 | High frequency |

Use these values when running custom predictions.

---

## 9. Common Use Cases

### Case 1: Pre-Deployment Check
```bash
# Run query on dev machine, get EXPLAIN plan
psql dev_db -c "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) $(cat query.sql)" > explain.json

# Check if it will meet SLA in production
venv/bin/python gate/cetp_gate.py \
  --explain-json explain.json \
  --config cetp.yml \
  --query-id my_query
```

### Case 2: Batch Query Validation
```bash
# Test multiple queries
for query in q1 q18 q19; do
    echo "Testing $query..."
    venv/bin/python gate/cetp_gate.py \
      --dev-features "examples/gate_queries/${query}_c7i_dev_features.json" \
      --config cetp.yml \
      --query-id $query
done
```

### Case 3: Hardware Comparison
```bash
# Compare same query on different dev machines
venv/bin/python gate/cetp_gate.py \
  --dev-features examples/gate_queries/q19_c5a_dev_features.json \
  --config cetp.yml --query-id q19

venv/bin/python gate/cetp_gate.py \
  --dev-features examples/gate_queries/q19_c7i_dev_features.json \
  --config cetp.yml --query-id q19
```

---

## 10. Interactive Menu

For a guided experience:

```bash
./run.sh
```

**Menu Options:**
1. Run Gate Demo (4 verdict scenarios)
2. Run Online Self-Calibration Demo
3. Run Test Suite (verify everything works)
4. Start Research Dashboard (web visualization)
5. Check Dependencies (verify installation)
6. Exit

---

## Need More Help?

- **Troubleshooting:** See `SETUP_GUIDE.md`
- **Full Documentation:** See `README.md` and `RUNNING.md`
- **Research Details:** See `results/phase7_report.md`
- **Setup Summary:** See `SETUP_SUMMARY.md`

---

**Ready to use!** Start with the demo, then try custom predictions. 🚀
