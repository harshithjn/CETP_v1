# 🚀 CETP v1 - Quick Start Guide

## Status: ✅ Ready to Use

Everything is installed and working. Start here for the fastest way to see the system in action.

## 30-Second Demo

```bash
cd /home/kartik/Desktop/capstone/CETP_v1
venv/bin/python gate/cetp_gate.py --demo
```

This will run 4 scenarios showing all verdict types (PASS, BLOCK, WARN, low-confidence).

## Interactive Menu

For a guided experience with all features:

```bash
./run.sh
```

Select from:
1. Gate Demo
2. Online Self-Calibration Demo  
3. Run Test Suite
4. Start Research Dashboard
5. Check Dependencies

## What This Project Does

**Problem:** Query performance doesn't transfer across different hardware. A query that's fast in development might be slow in production.

**Solution:** CETP predicts production query runtime using:
- Query plan from development machine
- Static hardware specifications for both machines
- Machine learning models

**Output:** CI/CD gate that blocks deployments likely to violate SLAs.

## Key Commands

### See All Verdict Types
```bash
venv/bin/python gate/cetp_gate.py --demo
```
Shows: PASS, BLOCK, WARN, and low-confidence scenarios

### Watch The System Learn
```bash
venv/bin/python scripts/analysis/addition7_online_loop.py --demo
```
Shows: Error reduction from 77.7% to 16.1% after learning from 12 measurements

### Verify Everything Works
```bash
venv/bin/pytest tests/test_e2e.py -v
```
Runs: 28 comprehensive tests (all passing)

### View Research Dashboard
```bash
venv/bin/python -m http.server 8000 --directory dashboard
```
Then open: http://localhost:8000

## Example Output

When you run the gate demo, you'll see predictions like:

```
CETP Gate verdict for q19: PASS
  dev time: 43.7 ms
  predicted prod runtime: p50=59.1 ms  p95=79.9 ms  p99=129.6 ms
  confidence: 1.00
  reason: predicted p99 (129.6 ms) clears SLA (200.0 ms)
```

## What's Already Done

✅ Repository cloned  
✅ Virtual environment created  
✅ All dependencies installed  
✅ scikit-learn 1.8.0 (correct version)  
✅ Gate demo tested - working  
✅ Test suite run - 28/28 passing  
✅ Online learning demo tested - working  

## Files You Should Know About

- `SETUP_SUMMARY.md` - Complete setup details and verification
- `SETUP_GUIDE.md` - Detailed documentation and troubleshooting  
- `README.md` - Original project README with research details
- `RUNNING.md` - All usage scenarios explained
- `run.sh` - Interactive menu script

## Troubleshooting

### If something doesn't work:

**Check scikit-learn version:**
```bash
venv/bin/pip show scikit-learn | grep Version
```
Should show: 1.8.0

**Reinstall if needed:**
```bash
venv/bin/pip install --force-reinstall scikit-learn==1.8.0
```

**Verify models are present:**
```bash
ls -lh models/*.pkl
```
Should show 3+ .pkl files

## Project Structure

```
CETP_v1/
├── gate/               ← Main entry point (start here)
├── models/             ← Pre-trained ML models
├── data/               ← Training datasets
├── scripts/            ← Analysis scripts
├── tests/              ← Test suite
├── results/            ← Generated reports
└── dashboard/          ← Web visualization
```

## What To Do Next

1. **Run the demos** (commands above) to see it in action
2. **Read the research** in `results/phase7_report.md`  
3. **Explore the code** starting with `gate/cetp_gate.py`
4. **Try custom queries** with your own EXPLAIN plans

## Getting Help

- Check `SETUP_GUIDE.md` for detailed documentation
- Read `RUNNING.md` for all usage scenarios
- See `SETUP_SUMMARY.md` for what was installed

## Academic Context

This is a capstone research project demonstrating ML-based cross-environment query performance prediction for TPC-H analytical workloads on PostgreSQL across EC2 instance types.

**Key Insight:** A single bandwidth ratio is a strong baseline, improved upon by multi-core compute throughput measurement (21.60% MAPE vs 23.03% baseline).

---

**Ready to use!** Start with the 30-second demo above. ✨
