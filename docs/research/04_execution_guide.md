# 04 — Execution guide (executed vs inferred)

Environment used: macOS (Darwin 25.6, arm64 [DED from docs]), Python 3.13.2, scikit-learn 1.8.0, numpy 2.5.3, pandas 2.2.3, scipy 1.18.1 — these already matched `requirements.txt` pins; I did **not** do a clean install.

| Step | Command | Status |
|---|---|---|
| Prereqs | Python ≥3.11 (`pyproject`), but numpy 2.5.3 has no 3.11 wheel per workflow comment → use 3.13 | inferred |
| Install package | `pip install -e .` (zero runtime deps) | **not run**; package imports worked via `python3 -m cetp.cli` from repo root |
| Install research deps | `make install` (`pip install -r requirements.txt`) | not run (already satisfied) |
| Verify | `python3 -m cetp.cli selftest` | **run: PASSED** (redaction, prediction 3587→2122 ms scaling 0.592 c7i→c5a, regression identity, calibration, operator model) |
| Tests | `python3 -m pytest tests -q` | **run: 108 passed** in 13.4 s, 23,028 NumPy-2.5 `array.shape` DeprecationWarnings from joblib unpickling. Note: `make test` and README run only `tests/test_e2e.py` (28 tests) |
| Regenerate headline | `make results` (`scripts/analysis/canonical_evaluation.py`) | **run (via python3 directly): OK**, 2m21s, output matches committed JSON |
| Determinism | `make verify-determinism` | not run |
| Gate demo | `python3 gate/cetp_gate.py --demo` | **run: OK**; writes `results/phase8_cetp_gate_demo.md` |
| Regress | `cetp regress --before b.json --after a.json --dev c7i --prod c5a --fail-on-regression` | not run directly; exercised by selftest/tests |
| Per-operator predict | `cetp predict-prod --dev-plan … --prod-plan … --dev X --prod Y --calibration k` | not run directly; covered by `tests/test_operator_model.py` |
| Probe a machine | `make probe-here` (needs live PostgreSQL, idle machine) | **not run** |
| Container grid | `make grid-setup` (Docker, ~10 min), `make grid-collect` (~2–3 h) | **not run**; Docker/PostgreSQL not exercised |
| x86 probe | GitHub Actions `x86-operator-probe.yml` | not run (CI only) |
| Docs | `make docs-all` (needs Chrome/Chromium) | not run |
| Extension | `vscode-extension`: `npm install`, `tsc`, `.vsix` already built (0.2.4) | not run |

**Environment variables:** `CETP_CALIBRATION_PATH` (default `~/.cetp/calibration.json`), `CETP_TELEMETRY_PATH` (default `~/.cetp/telemetry.jsonl`), `MACHINE_ID`, `PGDATABASE` (Makefile). No secrets found in the files I read.
**Common failures (evidence-based):**
- unpinned scikit-learn → `ModuleNotFoundError: No module named '_loss'` when unpickling quantile models (requirements.txt comment, CI history).
- Cross-ISA prediction without calibration → `CrossArchitectureUncalibrated` (by design).
- `load_rates` raising on v1 signatures, invalid probes or `reliable:false` (re-probe on an idle machine).
- Docker build: macOS credential helper hang → `make artifact` uses a no-auth `DOCKER_CONFIG`.
**Reset:** delete `~/.cetp/`; `make promote-models` expects `models_candidate/` (does not exist now) and backs up to `models_backup/`.
