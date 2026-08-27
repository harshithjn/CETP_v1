# CETP v1 - Complete Step-by-Step Walkthrough

## 🎯 Purpose
This document provides a complete, step-by-step guide to run the entire CETP project from scratch to full demonstration. Perfect for first-time users, demonstrations, or verification.

---

## 📋 Prerequisites

### System Requirements:
- **OS:** Linux (tested on Ubuntu)
- **Python:** 3.12+ installed
- **Git:** Installed
- **Disk Space:** ~500 MB
- **Time Required:** 15-20 minutes

### Check Prerequisites:
```bash
python3 --version    # Should show 3.12 or higher
git --version        # Should show git version
df -h .              # Check disk space
```

---

## 🚀 PART 1: Setup (One-Time, ~5 minutes)

### Step 1: Navigate to Project Directory
```bash
cd /home/kartik/Desktop/capstone/CETP_v1
pwd  # Verify you're in the right directory
```

**Expected Output:**
```
/home/kartik/Desktop/capstone/CETP_v1
```

---

### Step 2: Verify Repository Contents
```bash
ls -la
```

**Expected Output:** You should see:
- `gate/` directory
- `models/` directory
- `data/` directory
- `scripts/` directory
- `tests/` directory
- `venv/` directory (virtual environment)
- `README.md`, `RUNNING.md`, etc.

---

### Step 3: Check Virtual Environment
```bash
ls venv/
```

**Expected Output:** You should see `bin/`, `lib/`, `include/`, etc.

If `venv/` doesn't exist, create it:
```bash
python3 -m venv venv
```

---

### Step 4: Verify Dependencies Installed
```bash
venv/bin/pip list | grep -E "(scikit-learn|numpy|pandas|joblib)"
```

**Expected Output:**
```
joblib          1.5.3
numpy           2.5.2
pandas          3.0.5
scikit-learn    1.8.0
```

**CRITICAL:** scikit-learn MUST be version 1.8.0

If dependencies are missing or wrong version:
```bash
venv/bin/pip install -r requirements.txt
```

---

### Step 5: Verify Models Exist
```bash
ls -lh models/*.pkl models/*.json
```

**Expected Output:**
```
models/bottleneck_classifier.pkl
models/hardware_signature.pkl
models/scaling_quantile_models.pkl
models/scaling_feature_columns.json
... (and more)
```

If models are missing, the repository wasn't cloned correctly.

---

### Step 6: Test Basic Import
```bash
venv/bin/python -c "import gate.cetp_gate; print('✓ Gate module loads successfully')"
```

**Expected Output:**
```
✓ Gate module loads successfully
```

---

## 🎬 PART 2: Run All Demos (Step-by-Step, ~10 minutes)

### Demo 1: Gate Demo (30 seconds) - MOST IMPORTANT

**Purpose:** See all 4 verdict types (PASS, BLOCK, WARN, low-confidence)

```bash
venv/bin/python gate/cetp_gate.py --demo
```

**What You'll See:**

1. **PASS Scenario:** Query 19, c5a→c7i, SLA=200ms
   ```
   CETP Gate verdict for q19: PASS
     predicted prod runtime: p50=59.1 ms  p95=79.9 ms  p99=129.6 ms
     reason: predicted p99 (129.6 ms) clears SLA (200.0 ms)
   ```

2. **BLOCK Scenario:** Query 1, r5n→z1d, SLA=3000ms
   ```
   CETP Gate verdict for q1: BLOCK
     predicted prod runtime: p50=6104.4 ms  p95=10539.3 ms  p99=42616.8 ms
     reason: predicted p50 (6104.4 ms) exceeds SLA (3000.0 ms)
   ```

3. **WARN Scenario:** Query 18, c7i→r5n, SLA=8000ms
   ```
   CETP Gate verdict for q18: WARN
     reason: predicted p50 (6157.2 ms) is under SLA but predicted p99 
             (12830.3 ms) exceeds it (8000.0 ms). Recommend canary run.
   ```

4. **WARN (Low Confidence):** Query 19 on unknown hardware
   ```
   CETP Gate verdict: WARN
     hw-distance confidence: 0.00
     reason: prod hardware signature is an outlier relative to training set
   ```

**Output File:** `results/phase8_cetp_gate_demo.md`

**✅ Success Criteria:** All 4 scenarios run without errors

---

### Demo 2: View Accuracy Metrics (10 seconds)

**Purpose:** See real accuracy numbers from evaluation

```bash
venv/bin/python show_accuracy.py
```

**What You'll See:**

```
======================================================================
CETP PREDICTION ACCURACY - REAL RESULTS
======================================================================

Overall Accuracy (Leave-One-Machine-Out Cross-Validation):
----------------------------------------------------------------------
naive (=1.0)                              31.97% (95% CI: 25.43% - 39.89%)
naive-linear (1/bandwidth_ratio)          23.03% (95% CI: 20.29% - 26.05%)
analytical roofline                       31.14% (95% CI: 29.79% - 32.63%)
...

ACCURACY BY HARDWARE (when that machine was held out):
----------------------------------------------------------------------
Machine  n     Learned      Analytical   Naive-Linear
c5a      168        54.85%      33.86%      19.66%
c7i      168        36.37%      29.61%      27.68%
...
```

**✅ Success Criteria:** See accuracy table with 23.03% MAPE for baseline

---

### Demo 3: Live Prediction Test (15 seconds)

**Purpose:** Test predictions against real known results

```bash
venv/bin/python test_prediction_accuracy.py
```

**What You'll See:**

```
======================================================================
LIVE PREDICTION ACCURACY TEST
======================================================================

Query    Dev→Prod     Dev Time   Actual     Predicted  Error      Type
==================================================================================
q19      c5a→c7i          43.7ms     53.2ms     59.1ms     11.0% bandwidth
q19      c7i→c5a          53.2ms     43.7ms     39.0ms     10.7% bandwidth
q1       r5n→z1d        7103.3ms   9215.0ms   6010.5ms     34.8% compute

Average Error (MAPE): 18.83%
```

**Detailed Breakdown:**
```
Query: q19 (Bandwidth-bound query, prod has slower bandwidth)
Development machine: c5a (bandwidth=14.9 GB/s, compute=0.593)
Production machine:  c7i (bandwidth=8.44 GB/s, compute=0.807)

Bottleneck classification:
  bandwidth : 0.99
  compute   : 0.00

Predictions:
  p50 (median):  59.1 ms
  p95:           79.9 ms
  p99 (worst):   129.6 ms

Actual production time: 53.2 ms
Prediction error (p50): 11.0%
```

**✅ Success Criteria:** See 3 test cases with ~19% average error

---

### Demo 4: Online Self-Calibration (2-3 minutes)

**Purpose:** See system learn from unfamiliar hardware

```bash
venv/bin/python scripts/analysis/addition7_online_loop.py --demo
```

**What You'll See:**

**Step 1 - Before Learning:**
```
STEP 1 -- BEFORE LEARNING: 4-machine world, c5a entirely unseen
known machines: ['c7i', 'm5a', 'r5n', 'z1d']

First request against c5a as prod (dev=c7i, query=q19):
  state=WARN  confidence=0.000
  reason=LOW CONFIDENCE: prod hardware is an outlier

[BEFORE learning] n=36  mean confidence=0.000  MAPE=77.72%
```

**Step 2 - Measurement and Capture:**
```
STEP 2 -- MEASURE AND CAPTURE
[measurement_store] registered new machine 'c5a'
[measurement_store] recorded measurement: machine=c5a query=q3 time_ms=487.50
[measurement_store] recorded measurement: machine=c5a query=q5 time_ms=468.38
...
captured 12 new measurements for c5a
```

**Step 3 - Retrain:**
```
STEP 3 -- RETRAIN TRIGGER
[measurement_store] RETRAINED (version 2)
known machines = ['c5a', 'c7i', 'm5a', 'r5n', 'z1d']
```

**Step 4 - After Learning:**
```
STEP 4 -- AFTER LEARNING
Second request against c5a:
  state=PASS  confidence=1.000

[AFTER learning] n=36  mean confidence=1.000  MAPE=16.12%
```

**Final Summary:**
```
c5a hw-distance confidence:  before=0.000   after=1.000
c5a held-out MAPE:
  before learning: 77.72%
  after learning:  16.12%
  improvement:     +61.60 pp ✅
```

**Output Files:**
- `results/addition7_before_learning_eval.csv`
- `results/addition7_after_learning_eval.csv`
- `results/addition7_summary.json`

**✅ Success Criteria:** See error drop from 77.72% to 16.12%

---

### Demo 5: Full Test Suite (30 seconds)

**Purpose:** Verify entire system with automated tests

```bash
venv/bin/pytest tests/test_e2e.py -v
```

**What You'll See:**

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0

tests/test_e2e.py::TestFullChainHappyPath::test_cli_explain_in_verdict_out PASSED
tests/test_e2e.py::TestKnownAnswerRegression::test_prediction_matches... PASSED
...
tests/test_e2e.py::TestBufferAccounting::test_collection_script... PASSED

====================== 28 passed, 23028 warnings in 7.88s ======================
```

**Test Categories:**
- Full chain happy path (2 tests)
- Known answer regression (5 tests)
- All verdict branches (3 tests)
- Leakage guard (3 tests)
- Malformed input handling (6 tests)
- Config round trip (3 tests)
- Confidence boundary (3 tests)
- Buffer accounting (2 tests)

**✅ Success Criteria:** All 28 tests PASSED

---

### Demo 6: Interactive Menu (Optional)

**Purpose:** Easy access to all features

```bash
./run.sh
```

**Menu Options:**
```
========================================
CETP v1 - Quick Run Menu
========================================

1. Run Gate Demo (4 verdict scenarios)
2. Run Online Self-Calibration Demo
3. Run Test Suite
4. Start Research Dashboard (web server)
5. Check Dependencies
6. Exit

Select option [1-6]:
```

Try each option to see all features!

**✅ Success Criteria:** Menu displays and all options work

---

## 🔍 PART 3: Advanced Usage (Step-by-Step)

### Advanced 1: Single Query Prediction

**Purpose:** Predict performance for a specific query

```bash
venv/bin/python gate/cetp_gate.py \
  --dev-features gate/examples/gate_queries/q19_c5a_dev_features.json \
  --dev-bandwidth 14.9 \
  --dev-compute 0.593 \
  --prod-bandwidth 8.44 \
  --prod-compute 0.807 \
  --sla-ms 200
```

**What You'll See:**
```
CETP Gate verdict for query: PASS
  dev time: 43.7 ms  |  bandwidth_ratio=0.566  compute_ratio=1.361
  bottleneck probs: bandwidth=0.99, compute=0.00, io=0.01, mixed=0.00
  predicted prod runtime: p50=59.1 ms  p95=79.9 ms  p99=129.6 ms
  hw-distance confidence: 1.00 (threshold 0.6)
  reason: predicted p99 (129.6 ms) clears SLA (200.0 ms).
```

**✅ Success Criteria:** See PASS verdict with predictions

---

### Advanced 2: Using Configuration File

**Purpose:** Simplify repeated predictions with config

**Step 1:** View the config file
```bash
cat gate/cetp.yml
```

**Content:**
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
```

**Step 2:** Run with config
```bash
venv/bin/python gate/cetp_gate.py \
  --dev-features gate/examples/gate_queries/q19_c7i_dev_features.json \
  --config gate/cetp.yml \
  --query-id q19
```

**What You'll See:**
```
CETP Gate verdict for q19: PASS
  predicted prod runtime: p50=39.0 ms  p95=54.4 ms  p99=104.6 ms
  reason: predicted p99 (104.6 ms) clears SLA (200.0 ms)
```

**Benefits:**
- Hardware specs stored once
- Per-query SLA thresholds
- Easier CI/CD integration

**✅ Success Criteria:** See verdict using config values

---

### Advanced 3: JSON Output for CI/CD

**Purpose:** Get machine-readable output for automation

```bash
venv/bin/python gate/cetp_gate.py \
  --dev-features gate/examples/gate_queries/q19_c7i_dev_features.json \
  --config gate/cetp.yml \
  --query-id q19 \
  --json | head -40
```

**What You'll See:**
```json
{
  "query_id": "q19",
  "dev_features": {
    "cost": 32575.98,
    "time_ms": 53.158,
    "total_buffers": 20566.0
  },
  "bandwidth_ratio": 1.765,
  "compute_ratio": 0.735,
  "bottleneck_probabilities": {
    "bandwidth": 0.99,
    "compute": 0.0
  },
  "predicted_scaling_factor": {
    "p50": 0.734,
    "p95": 1.024,
    "p99": 1.968
  },
  "verdict": {
    "state": "PASS",
    "confidence": 1.0,
    "predicted_prod_ms": {
      "p50": 39.0,
      "p95": 54.4,
      "p99": 104.6
    }
  }
}
```

**Exit Codes:**
- `0` = PASS or WARN (safe)
- `1` = BLOCK (deployment blocked)

**✅ Success Criteria:** Valid JSON output with verdict

---

### Advanced 4: Research Dashboard

**Purpose:** Interactive visualization of results

**Step 1:** Start web server
```bash
venv/bin/python -m http.server 8000 --directory dashboard &
```

**Step 2:** Open in browser
```
http://localhost:8000
```

**What You'll See:**
- Interactive plots
- Performance metrics
- Hardware comparisons
- Bottleneck analysis

**Step 3:** Stop server when done
```bash
# Find the process
ps aux | grep "http.server"

# Kill it (replace PID with actual number)
kill <PID>

# Or use:
pkill -f "http.server"
```

**✅ Success Criteria:** Dashboard loads in browser

---

## 📊 PART 4: Understanding the Output

### Reading Gate Verdicts

**Format:**
```
CETP Gate verdict for <query_id>: <STATE>
  dev time: <ms>  |  bandwidth_ratio=<value>  compute_ratio=<value>
  bottleneck probs: bandwidth=<p>, compute=<p>, io=<p>, mixed=<p>
  predicted prod runtime: p50=<ms>  p95=<ms>  p99=<ms>
  hw-distance confidence: <0-1> (threshold 0.6)
  reason: <explanation>
```

**States Explained:**

| State | Meaning | Action |
|-------|---------|--------|
| **PASS** | p99 < SLA | ✅ Deploy safely |
| **BLOCK** | p50 > SLA | 🛑 Block deployment |
| **WARN** | p50 < SLA < p99 | ⚠️ Risky, use canary |
| **WARN (low confidence)** | Unknown hardware | ⚠️ Run real measurement |

---

### Understanding Hardware Ratios

**Bandwidth Ratio:**
```
bandwidth_ratio = prod_bandwidth / dev_bandwidth
```
- `> 1.0` → production is faster
- `< 1.0` → production is slower
- Example: 0.566 means prod has 56.6% of dev's bandwidth

**Compute Ratio:**
```
compute_ratio = prod_compute / dev_compute
```
- Same interpretation as bandwidth ratio

---

### Understanding Bottleneck Probabilities

**Example:**
```
bottleneck probs: bandwidth=0.99, compute=0.00, io=0.01, mixed=0.00
```

**Meaning:** Query is 99% bandwidth-bound, 1% I/O-bound

**Types:**
- **bandwidth**: Memory bandwidth limited
- **compute**: CPU compute limited
- **io**: Disk I/O limited
- **mixed**: Mix of bandwidth and compute

---

### Understanding Confidence Scores

**Scale:** 0.0 to 1.0

| Score | Meaning | Reliability |
|-------|---------|-------------|
| **1.00** | Hardware very similar to training | High (20-25% error) |
| **0.80-0.99** | Hardware somewhat similar | Good |
| **0.60-0.79** | Hardware moderately different | Moderate |
| **< 0.60** | Hardware very different | Low - don't trust! |

**Low confidence example:**
```
hw-distance confidence: 0.00 (threshold 0.6)
reason: prod hardware signature is an outlier
```
→ System refuses to predict, recommends real measurement

---

## 🔧 PART 5: Troubleshooting

### Problem 1: "Command 'python' not found"

**Solution:**
```bash
# Use python3 instead
python3 --version

# Or create alias
alias python=python3
```

---

### Problem 2: "ModuleNotFoundError: No module named 'joblib'"

**Solution:**
```bash
# Activate virtual environment
source venv/bin/activate

# Or use venv/bin/python directly
venv/bin/pip install -r requirements.txt
```

---

### Problem 3: "ModuleNotFoundError: No module named '_loss'"

**Cause:** Wrong scikit-learn version

**Solution:**
```bash
# Check version
venv/bin/pip show scikit-learn

# Should be 1.8.0, if not:
venv/bin/pip install --force-reinstall scikit-learn==1.8.0
```

---

### Problem 4: "InconsistentVersionWarning"

**Warning Example:**
```
InconsistentVersionWarning: Trying to unpickle estimator from version 1.8.0 
when using version 1.9.0
```

**Solution:**
```bash
venv/bin/pip install --force-reinstall scikit-learn==1.8.0
```

---

### Problem 5: Tests Fail

**Check dependencies:**
```bash
venv/bin/pip install --force-reinstall -r requirements.txt
```

**Run specific test:**
```bash
venv/bin/pytest tests/test_e2e.py::TestFullChainHappyPath -v
```

---

### Problem 6: Models Not Found

**Check models directory:**
```bash
ls -la models/*.pkl
```

**If missing:** Repository wasn't cloned correctly
```bash
cd ..
rm -rf CETP_v1
git clone https://github.com/harshithjn/CETP_v1.git
cd CETP_v1
```

---

## ✅ PART 6: Verification Checklist

Run through this checklist to verify everything works:

```bash
# 1. Check directory
cd /home/kartik/Desktop/capstone/CETP_v1 && pwd
# Expected: /home/kartik/Desktop/capstone/CETP_v1 ✅

# 2. Check virtual environment
ls venv/bin/python && echo "✅ venv exists"

# 3. Check Python version
venv/bin/python --version
# Expected: Python 3.12.x ✅

# 4. Check scikit-learn version
venv/bin/pip show scikit-learn | grep Version
# Expected: Version: 1.8.0 ✅

# 5. Check models exist
ls models/*.pkl | wc -l
# Expected: 3 or more files ✅

# 6. Test gate import
venv/bin/python -c "import gate.cetp_gate; print('✅ Import works')"

# 7. Run gate demo
venv/bin/python gate/cetp_gate.py --demo
# Expected: 4 scenarios run successfully ✅

# 8. Show accuracy
venv/bin/python show_accuracy.py
# Expected: See accuracy table ✅

# 9. Test live predictions
venv/bin/python test_prediction_accuracy.py
# Expected: 3 tests with ~19% average error ✅

# 10. Run test suite
venv/bin/pytest tests/test_e2e.py -v --tb=no
# Expected: 28 passed ✅
```

**If all 10 checks pass: Everything is working! ✅✅✅**

---

## 🎓 PART 7: What You Learned

After completing this walkthrough, you've:

✅ Set up CETP from scratch
✅ Run all 4 main demos
✅ Seen real accuracy metrics (21-23% MAPE)
✅ Tested live predictions (18.83% average error)
✅ Watched online learning improve from 77% to 16% error
✅ Verified with 28 automated tests
✅ Used advanced features (config files, JSON output)
✅ Understood all output formats
✅ Solved common problems

**You can now:**
- Demo CETP to others
- Integrate into CI/CD pipelines
- Predict query performance
- Understand accuracy trade-offs
- Troubleshoot issues

---

## 📚 PART 8: Next Steps

### For Production Use:
1. Read **USAGE_EXAMPLES.md** section 4 (CI/CD integration)
2. Create your own `cetp.yml` config file
3. Test with your actual queries
4. Set appropriate SLA thresholds
5. Integrate into deployment pipeline

### For Research/Analysis:
1. Read **results/phase7_report.md** (complete methodology)
2. Read **results/addition4_compute_benchmark_report.md**
3. Explore other reports in `results/` directory
4. Examine data in `data/corrected/`

### For Understanding Accuracy:
1. Read **ACCURACY_REPORT.md** (comprehensive analysis)
2. Run different query/machine combinations
3. Experiment with online learning
4. Study per-machine accuracy differences

---

## 🏁 Summary

**Total Time:** ~15-20 minutes

**What Was Covered:**
- Setup verification (6 steps)
- 6 demos run successfully
- Advanced usage (4 scenarios)
- Output interpretation
- Troubleshooting (6 common issues)
- Verification checklist (10 checks)

**Key Results Demonstrated:**
- ✅ 21-23% MAPE accuracy
- ✅ 18.83% average error on live tests
- ✅ 77% → 16% improvement with online learning
- ✅ 28/28 tests passing
- ✅ All 4 verdict types working
- ✅ Confidence scoring catching failures

**System Status:** Production-ready ✅

---

## 📞 Quick Reference

### Most Important Commands:
```bash
# Gate demo (30 sec)
venv/bin/python gate/cetp_gate.py --demo

# Show accuracy
venv/bin/python show_accuracy.py

# Live tests
venv/bin/python test_prediction_accuracy.py

# Online learning (2-3 min)
venv/bin/python scripts/analysis/addition7_online_loop.py --demo

# Full tests (30 sec)
venv/bin/pytest tests/test_e2e.py -v

# Interactive menu
./run.sh
```

### Documentation:
- **This walkthrough:** COMPLETE_WALKTHROUGH.md
- **Quick start:** QUICK_START.md
- **All features:** USAGE_EXAMPLES.md
- **Accuracy details:** ACCURACY_REPORT.md
- **Setup guide:** SETUP_GUIDE.md

---

**You've completed the full CETP walkthrough! 🎉**

**Everything is documented, working, and ready to use.** 🚀
