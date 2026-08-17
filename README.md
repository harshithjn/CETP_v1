# Cross-Environment TPC-H Runtime Prediction (CETP)

CETP is a machine learning-driven framework designed to predict the runtime of analytical database queries (TPC-H) across heterogeneous hardware environments. Specifically, the system predicts execution runtimes on target production machines (e.g., AWS EC2 instance types like `c5a`, `c7i`, `m5a`, `r5n`, `z1d`) using execution data collected from a developer environment, combined with hardware specifications.

---

## 🚀 Quick Start & Demos

### 1. Interactive Research Dashboard (UI)
The project includes a high-fidelity research dashboard showing MAPE comparisons, hardware signal analysis, selective prediction risk-coverage curves, and more.

Run a local server from the project directory:
```bash
python3 -m http.server 8000 --directory dashboard
```
Then open your browser and go to: **http://localhost:8000**

---

### 2. SLA Gate Command-Line Demo
The SLA gate determines whether a query running on a development server can safely deploy to a production instance without violating Service Level Agreements (SLAs).

Run the worked demo:
```bash
python cetp_gate.py --demo
```
This showcases the 4 verdict paths:
- **`PASS`**: The predicted tail runtime ($p_{99}$) clears the SLA.
- **`BLOCK`**: The predicted median runtime ($p_{50}$) exceeds the SLA.
- **`WARN`**: The median runtime is under SLA, but the tail runtime ($p_{99}$) breaches it.
- **`WARN (Low Confidence)`**: The target hardware is an outlier relative to the training set.

---

### 3. Online Self-Calibration Loop
To demonstrate how the system handles completely unseen hardware environments, run the online calibration simulation:
```bash
python addition7_online_loop.py
```
This loop:
1. Detects an unseen machine (e.g., `c5a`), flagging a `WARN (Low Confidence)` verdict.
2. Simulates streaming in 12 database execution measurements.
3. Automatically triggers online retraining.
4. Queries again, showing the confidence score rising to `1.00` and prediction error dropping from **77.7% to 15.0%**.

---

### 4. Run the Automated Tests
Run the `pytest` suite to verify model compatibility, database logic, and feature engineering pipelines:
```bash
pytest test_e2e.py
```

---

## 🛠 Repository Overview

```
.
├── dashboard/                  # HTML/JS Interactive Research Dashboard
│   ├── index.html              # Dashboard markup and interactive SVG visualizer
│   ├── data.js                 # Exported metrics, calibration results, and metadata
│   └── assets/                 # Generated plot images (learning curve, risk-coverage)
├── test_fixtures/              # PostgreSQL EXPLAIN JSON files and test SQLite database
├── results/                    # Research reports, evaluation metrics, and logs
├── cetp_gate.py                # SLA gate entrypoint and CLI interface
├── cetp_gate_demo.py           # Demo scenarios definition for the SLA gate
├── addition7_online_loop.py    # Online retraining loop and self-calibration script
├── measurement_store.py        # Database schema and model management (SQLite/scikit-learn)
├── test_e2e.py                 # Comprehensive end-to-end test suite
├── cetp.yml                    # Configuration file mapping hardware signatures and SLAs
├── tpch_dataset_corrected.csv  # Main TPC-H metrics dataset
└── .gitignore                  # Git ignore patterns
```

---

## 🧠 Technical Highlights

- **Explain Parser**: Parses standard PostgreSQL `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` outputs to extract precise feature counts (costs, shared hit/read page buffers, rows processed, runtime).
- **Bottleneck Classifier**: Uses a Random Forest classifier to identify whether the execution is bound by CPU (`compute`), Memory/Disk bandwidth (`bandwidth`), I/O (`io`), or a combination (`mixed`).
- **Quantile Regressor**: Predicts runtime scaling factors using Gradient Boosting Regressors (GBRs) fit at specific quantiles ($p_{50}, p_{95}, p_{99}$) to construct predictive tails.
- **Out-of-Distribution Detection**: Computes hardware distance confidence scores to prevent making confident predictions on machines that lie far outside the model's training envelope.
- **Self-Calibration Loop**: Learns hardware scaling behaviors on-the-fly with as few as 12 database executions, bypassing long offline profiling cycles.
