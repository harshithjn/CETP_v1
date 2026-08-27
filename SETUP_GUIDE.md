# CETP v1 Setup and Usage Guide

This guide documents the complete setup process and usage instructions for the Cross-Environment TPC-H Runtime Prediction (CETP) project.

## System Requirements

- Python 3.12+ (tested with Python 3.12.3)
- Linux operating system (tested on Ubuntu)
- Git

## Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/harshithjn/CETP_v1.git
cd CETP_v1
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
```

### 3. Activate Virtual Environment

```bash
source venv/bin/activate
```

### 4. Install Dependencies

All required dependencies are listed in `requirements.txt`:
- numpy
- pandas
- joblib
- scikit-learn==1.8.0 (specific version required for model compatibility)
- pyyaml
- matplotlib
- pytest

Install them with:

```bash
venv/bin/pip install -r requirements.txt
```

**Important:** scikit-learn version 1.8.0 is required because the pre-trained models were created with this version. Using a different version will cause compatibility errors.

## Running the Project

### Option 1: Quick Demo (Recommended First Step)

Run the gate demo to see all four verdict scenarios (PASS, BLOCK, WARN, and low-confidence WARN):

```bash
venv/bin/python gate/cetp_gate.py --demo
```

**What it does:**
- Loads pre-trained machine learning models
- Runs 4 pre-built query scenarios
- Demonstrates all verdict paths
- Takes about 30 seconds
- Saves report to `results/phase8_cetp_gate_demo.md`

### Option 2: Online Self-Calibration Demo

See the system learn from unfamiliar hardware:

```bash
venv/bin/python scripts/analysis/addition7_online_loop.py --demo
```

**What it does:**
- Shows prediction on unknown machine (low confidence)
- Simulates learning from measurements
- Retrains models with new data
- Demonstrates error reduction from 77.7% to 16.1%
- Saves results to `results/addition7_*.csv`

### Option 3: Run Test Suite

Verify the entire system with automated tests:

```bash
venv/bin/pytest tests/test_e2e.py -v
```

**Test Coverage:**
- 28 comprehensive end-to-end tests
- Full chain from EXPLAIN input to verdict
- Leakage checks
- Malformed input handling
- Known-answer regression tests
- All tests pass successfully

### Option 4: View Research Dashboard

Start a local web server to view the research dashboard:

```bash
venv/bin/python -m http.server 8000 --directory dashboard
```

Then open your browser to: `http://localhost:8000`

### Option 5: Run Gate Against Custom Query

To run the gate against a specific query with custom parameters:

```bash
venv/bin/python gate/cetp_gate.py \
  --dev-features path/to/features.json \
  --dev-bandwidth 10.5 \
  --dev-compute 0.65 \
  --prod-bandwidth 12.0 \
  --prod-compute 0.75 \
  --sla-ms 5000
```

Or use a configuration file:

```bash
venv/bin/python gate/cetp_gate.py \
  --explain-json path/to/explain.json \
  --config gate/cetp.yml \
  --query-id q1
```

## Project Structure

```
CETP_v1/
├── gate/                  # Main gate CLI and demo
│   ├── cetp_gate.py      # Core gate logic
│   ├── cetp_gate_demo.py # Demo scenarios
│   └── examples/         # Sample query plans
├── models/               # Pre-trained ML models
│   ├── bottleneck_classifier.pkl
│   ├── scaling_quantile_models.pkl
│   ├── hardware_signature.pkl
│   └── scaling_feature_columns.json
├── data/                 # Datasets
│   ├── raw/             # Original measurements
│   └── corrected/       # Bug-corrected data
├── scripts/             # Analysis and collection scripts
│   ├── analysis/        # Research evaluation scripts
│   └── collection/      # Data collection harness
├── tests/               # Test suite
├── results/             # Generated reports and figures
├── dashboard/           # Research dashboard (HTML/JS)
└── requirements.txt     # Python dependencies
```

## Troubleshooting

### ModuleNotFoundError: No module named 'joblib'

Make sure you've activated the virtual environment and installed dependencies:

```bash
source venv/bin/activate  # or venv/bin/pip install -r requirements.txt
```

### scikit-learn version mismatch errors

The models require scikit-learn 1.8.0. Reinstall if needed:

```bash
venv/bin/pip install --force-reinstall scikit-learn==1.8.0
```

### ModuleNotFoundError: No module named '_loss'

This occurs when using incompatible scikit-learn versions. Downgrade to 1.8.0 as shown above.

## Verification Checklist

✅ Repository cloned successfully
✅ Virtual environment created
✅ All dependencies installed (especially scikit-learn==1.8.0)
✅ Gate demo runs successfully (4 scenarios)
✅ All 28 tests pass
✅ Online learning demo completes successfully

## Key Results from Successful Run

When everything is set up correctly, you should see:

1. **Gate Demo Output:**
   - PASS scenario with confidence 1.00
   - BLOCK scenario with predicted p50 exceeding SLA
   - WARN scenario with p99 over threshold
   - Low confidence WARN with hw-distance = 0.00

2. **Test Suite:**
   - All 28 tests PASSED
   - Some deprecation warnings (expected, can be ignored)

3. **Online Learning Demo:**
   - Before learning: MAPE = 77.72%, confidence = 0.000
   - After learning: MAPE = 16.12%, confidence = 1.000
   - Improvement: +61.60 percentage points

## Next Steps

- Read `README.md` for project overview
- Read `RUNNING.md` for detailed usage scenarios
- Explore `results/` directory for research reports
- Check `results/phase7_report.md` for complete methodology

## Database Setup (Optional)

For running against a live PostgreSQL database with TPC-H data:

1. Install PostgreSQL
2. Create database: `psql -c "CREATE DATABASE tpch;"`
3. Load schema: `psql tpch -f scripts/collection/dss.ddl`
4. Load TPC-H scale factor 1 data (see `scripts/collection/README`)

This is only needed for generating fresh EXPLAIN plans, not for running the demos.

## License and Status

Academic capstone project. See repository for license details.

## Support

For issues or questions, refer to the original repository: https://github.com/harshithjn/CETP_v1
