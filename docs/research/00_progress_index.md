# CETP evidence pack — progress index and source inventory

Written 2026-10-27 on branch `x86-probe` (HEAD `38c4bf2`, 17 commits, 544 tracked files, working tree dirty — see 06).
Labels used throughout: **[OBS]** observed in code/data/run output by me; **[CLAIM]** stated in a doc/comment, not independently verified; **[DED]** my deduction; **[UNK]** unknown.

## 1. Source inventory

| Source | State | Notes |
|---|---|---|
| `cetp/*.py` (12 modules, ~2,100 lines) | **Read in full** | the shipped, installable package |
| `gate/cetp_gate.py`, `gate/cetp.yml`, `gate/cetp_gate_demo.py` | gate read in full; demo run; `cetp_gate_demo.py`, `cetp_predict_cli.py` not read | legacy pickle-based gate |
| `tests/` (108 tests, 3 files) | **Executed**; test names skimmed (product loop), bodies not audited | 108 passed, 13.4 s |
| `scripts/analysis/canonical_evaluation.py` (854 L) | **Executed**; code not read; output read to section 8 | determinism checked |
| `scripts/collection/operator_probe.py` (403 L) | header (70 lines) read only | |
| `results/*.json`, `*.csv` (≈75 files) | `canonical_metrics.json`, `operator_model_metrics.json`, `laptop_to_prod_metrics.json`, `laptop_to_ec2_metrics.json`, `PROVENANCE.json` read in part | rest **not inspected** |
| `data/` (25 MB) | schemas and row counts of 3 CSVs only | |
| `models/*.pkl`, `models/*.json` | `hardware_signature.pkl` loaded and printed; `operator_signature.json` keys listed; others not opened | |
| `.github/workflows/` (3) | `cetp-gate.yml`, `x86-operator-probe.yml` read (head); `cetp-regression-check.yml` **not read** | |
| `vscode-extension/` | manifest head, `backend.ts` read; other `src/*.ts` **not read** | has uncommitted changes |
| `README.md`, `Makefile`, `pyproject.toml`, `requirements.txt`, `Dockerfile` | **Read in full** | |
| `docs/*.pdf` (≈50 PDFs), `docs/INDEX.md` | INDEX read; **no PDF opened** | all other Markdown was deleted in `38c4bf2`; originals live in git history |
| `docs/paper/CETP_paper.tex` (887 L, untracked) | first 60 lines (abstract) read | |
| `site/index.html`, `dist/`, `models_backup/`, `models_online/` | **not inspected** | |
| `scripts/collection/*` (collect, container_grid, predict_prod, SQL queries) | file list only | |
| Git history | 17 commit subjects/dates; diffs not read | |
| Prior chat history / requirements / decisions | **Not accessible** | see 07 for what to paste |
| External: PostgreSQL, TPC-H data, EC2 instances, Docker | **Not accessed** | EC2 hosts are terminated per the x86 workflow comment [CLAIM] |

## 2. Side effects of my verification runs (disclosure)
- `python3 scripts/analysis/canonical_evaluation.py` created **untracked `results/CANONICAL.md`**. It overwrote `results/canonical_metrics.json` and other tracked results with **byte-identical content** (cmp + `diff -rq` against a pre-run copy: only `CANONICAL.md` is new).
- `python3 gate/cetp_gate.py --demo` wrote `results/phase8_cetp_gate_demo.md` (not shown by `git status`; probably git-ignored — not checked).
- Pre-run copy of `results/` is in the session scratchpad. This `docs/research/` directory is new; nothing existing was edited.

## 3. Coverage checklist (what remains)
1. Read `canonical_evaluation.py`, `operator_model_evaluation.py`, `container_grid.py`, `predict_prod.py`, `operator_probe.py` bodies.
2. Open the PDFs that the README names as authority (`CANONICAL`, `OPERATOR_MODEL`, `phase7_report`, `laptop_to_prod`, `cross_isa_prediction`).
3. Recover deleted Markdown from `git show 425a2c2:...` if PDF text extraction is poor.
4. Read the three test files' assertions and the 4 unread workflow/gate files.
5. Read all of `vscode-extension/src` and the uncommitted diff.
6. Read the full paper `.tex` and cross-check every number against `results/`.

Files in this pack: `01_overview_architecture.md`, `02_implementation_spec.md`, `03_data_and_results.md`, `04_execution_guide.md`, `05_history_decisions.md`, `06_gaps_and_traceability.md`, `07_questions_and_readiness.md`.

## 4. Update 2026-10-27 (second pass)
Second-pass reading and the method comparison are in `08_method_comparison_and_contribution.md`. C10 investigation and fix: `09_C10_investigation_and_fix.md`. Corrections to the first pass: (1) the Markdown sources of the PDFs were recovered from git history and read — the PDFs were still not opened; (2) the VS Code extension and `gate/cetp_predict_cli.py` were read: they expose Gen 1 only; (3) `operator_model_evaluation.py` was re-executed and does **not** reproduce the committed `operator_model_metrics.json` (finding C10); tracked results were restored, `git status` shows only the untracked additions listed in §2.
