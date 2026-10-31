# 09 — Finding C10: investigation, root cause, fix, verification

Date 2026-10-27. Environment: macOS arm64, Python 3.13.2, scikit-learn 1.8.0, numpy 2.5.3, pandas 2.2.3 (all match `requirements.txt`). Branch `x86-probe`, HEAD `38c4bf2`. Nothing committed.

## 1. Reproduction [all executed]
`python3 scripts/analysis/operator_model_evaluation.py` (what `make operator-results` runs) at HEAD → exit 0, but: **12 cells, 2,904 pairs**, 3/22 queries with differing plans, naive 54.03 %, operator 29.70 %, `scalar_localized_par` 26.94 %, bootstrap `scalar_localized_par − operator` −2.76 pp CI [−4.29, −1.47]. Committed JSON: **8 cells, 1,232 pairs**, 0/22 plans differ, naive 72.52 %, operator 24.95 %. The committed JSON could not be regenerated.
Baseline preserved before any change: `docs/research/baseline_c10/` (committed JSON/CSV, SHA-256 of every input and output, the unfixed-script 12-cell re-run). Input SHA-256 list is in `input_and_output_sha256.txt`.

## 2. Root cause — two independent defects, proven by experiment
**Defect A (input selection): the script globbed the directory.** `load_grid()` read every `data/container_grid/tpch_*.csv` and `main()` read every cell in `operator_signature_grid.json`. Commits `ece60ff`/`6197abf` (cross-ISA study) added four x86 GitHub-Actions cells (`tpch_gha-*.csv` and signatures) to that directory, so the "container grid" silently became a 12-cell mixed-ISA grid. The manifest `grid_manifest.json` has always listed exactly the 8 container cells.

**Defect B (stale outputs): the committed results were generated from a different probe state than the signature file committed beside them.** I checked out the generating commit `26649a1` in an isolated worktree (copying in the git-ignored `data/plans_grid/`) and ran the script as committed: it did **not** reproduce the committed JSON either (naive matched; operator 22.88 % not 24.95 %, unreliable cells different). The committed JSON's `parallel_speedup` values equal, to 16 digits, those in **`models/operator_signature.json` @26649a1**, not those in `data/container_grid/operator_signature_grid.json` @26649a1 (the script's own input). Substituting the models/ registry for the grid file in that worktree reproduced **both `operator_model_metrics.json` (parsed-equal) and `operator_model_pairs.csv` (byte-identical)**. So: the grid file was re-probed (`--probe-only --force-probe`) after the evaluation ran and before commit; the outputs were never regenerated. The two registries were then re-synchronised for the 8 cells by `ece60ff` (at HEAD they are identical for all 12 cells — verified).

**Not a calculation bug.** The evaluation arithmetic is unchanged and, given the same inputs, reproduces the committed numbers exactly (the Defect-B experiment). Contributing: no input hash was recorded, so nothing could reveal the divergence; `data/plans_grid/` is git-ignored (`.gitignore:121`), so a fresh clone cannot run the script at all; `make test` ran only the 28 `test_e2e.py` tests.

Third state: `results/operator_model.md` / OPERATOR_MODEL.md (operator 22.30 %, 2 unreliable cells, PC1 59.4 %, rank 4) matches neither the committed JSON nor any run I could construct from committed probe states. It was written from an uncommitted intermediate run; **unresolvable from the repository** (see §8).

## 3. Fix (smallest justified)
| File | Change |
|---|---|
| `scripts/analysis/operator_model_evaluation.py` | `grid_machine_ids()` reads the cell list from `grid_manifest.json`; `load_grid(ids)` and signature loading are restricted to it; missing signature → hard exit; new `input_provenance()` writes `inputs_sha256` (signature file, manifest, 8 CSVs) into the metrics JSON. **No metric, model, bootstrap or seed code touched.** |
| `.gitignore` | negation `!data/plans_grid/` (268 files, ~1 MB) so the required plans can be committed. Files are **not yet `git add`ed** — your decision. |
| `Makefile` | `make test` now runs `tests/` (was only `test_e2e.py`) so the new tests run. |
| `tests/test_operator_evaluation.py` (new, 11 tests) | see §5 |
| `results/operator_model_metrics.json`, `operator_model_pairs.csv` | **regenerated** from the authoritative inputs (the committed `operator_signature_grid.json`, the script's declared input and the most recent probe state; it is also what `models/operator_signature.json` now holds). Old versions are in git and in `baseline_c10/`. |

Why regenerate rather than force a match to the old numbers: the old outputs are valid for a probe state that is no longer the project's committed input. Reproducing them would mean running on a stale registry. Per the integrity rule, the outputs were updated and the discrepancy is documented here.

## 4. Before / after (8 cells, 1,232 pairs, MAPE %)
| predictor | committed (stale probes) | regenerated | reliable-subset old → new |
|---|---:|---:|---|
| naive | 72.52 | 72.52 | 74.99 → 80.63 |
| scalar_seq | 73.60 | 70.98 | 75.92 → 78.94 |
| scalar_geo | 76.29 | 66.97 | 77.63 → 74.38 |
| operator_serial | 76.21 | 69.00 | 75.89 → 76.73 |
| scalar_geo_par | 29.90 | 30.15 | 30.06 → 31.41 |
| scalar_localized_par | 24.58 | **22.23** | 22.61 → 22.70 |
| **operator** | **24.95** | **22.41** | 22.23 → 22.89 |
| unreliable cells | c1m1g, c2m1g, c4m1g, c4m3g | c2m1g only (cv 0.307) | |
| reliable subset | 4 cells / 264 pairs | 7 cells / 924 pairs | |

Bootstrap (positive = second better; cluster = prod machine): `scalar_localized_par − operator` **−0.37 [−0.84, +0.01] → −0.17 [−0.49, +0.10]**, not significant both times. `scalar_geo_par − scalar_localized_par` +5.32 [0.14, 12.04] → **+7.92 [1.45, 16.31]**, significant. `naive − operator` +47.57 → +50.11, significant. `scalar_geo_par − operator` +4.96 [−0.52, +11.65] (ns) → **+7.74 [+1.08, +16.05]** (significant). Parallel-speedup spread 3.52× → 3.57×; max serial-axis spread 1.28× → 1.39×. Few-shot `spread`: k=0 22.41; k=1 **25.26** (worse than uncalibrated, as the project already warned); k=2 21.30; k=8 21.02.
**Signature dimensionality changed and weakens a documented claim:** PC1 59.6 % / mean |r| 0.42 / rank 3 (committed JSON) → **72.4 % / 0.652 / rank 3**; Markdown/paper-era claim "PC1 59.4 %, rank 4, mean|r| 0.487" is supported by neither.
**Qualitative conclusions unchanged:** (1) without a parallelism term, nothing beats naive meaningfully (66.97–70.98 vs 72.52); (2) localising the parallel term helps, significantly; (3) per-operator rate weighting is statistically indistinguishable from one scalar (−0.17 pp, CI spans 0). Quantitative figures changed by up to ~2.5 pp and several are now different.

## 5. Verification [all executed; outputs in scratchpad]
- Fixed evaluation, **two consecutive runs**: both exit 0; stdout identical; `operator_model_metrics.json` SHA-256 `2863b1f8…b4106` and `operator_model_pairs.csv` `8b2b8bfb…f4b3e` **byte-identical across runs**.
- New tests: `pytest tests/test_operator_evaluation.py` → **11 passed**. Full suite `pytest tests -q` → **119 passed** (108 + 11), 17 s.
- Mutation checks (temporary, reverted, `cmp` confirmed restoration): (i) restoring the old committed results → `test_committed_metrics_were_generated_from_the_committed_inputs` and `test_regeneration_reproduces_the_committed_results` **fail**; (ii) reintroducing the directory glob → grid-membership, provenance and regeneration tests **fail** (3 failed, 8 passed). After mutation (ii) I strengthened `test_extra_files_in_the_grid_directory…` so it also constructs the stray file before selecting the grid; **I re-ran the full suite (119 passed) but did not re-run mutation (ii) against that specific test.**
- Tests cover: grid = manifest cells and no `gha-*`; stray CSV ignored; input hashing; `mape`/`mdape`/`qerr` known values and symmetry; seeded, signed `cluster_bootstrap`; `few_shot_curve` k=0 identity; provenance of committed results; full regeneration equals committed (rtol 1e-9, CSV byte-equal); two-run determinism. Tests skip, not fail, if `data/plans_grid` is absent.

## 6. Affected artifacts (need updating by the owner; not edited by me)
`docs/paper/CETP_paper.tex` (24.95/72.52/76.21/76.29/73.60/29.90/24.58 in abstract, Table l.469-482, figure l.502, text l.523, l.784); `site/index.html` (l.177-223: 72.52, 73.60, 24.95, 24.58); `docs/results/operator_model.pdf`, `docs/OPERATOR_MODEL.pdf`, README prose referring to 22.30 %/24.95 %; `results/CANONICAL.md` is Gen-1 only, unaffected. `laptop_to_prod_*` unaffected by this fix.

## 7. Related finding found while verifying (C16, **unresolved**)
`scripts/analysis/laptop_to_prod_evaluation.py` does not reproduce its committed output exactly either: operator MAPE 43.7167 % committed vs 43.7070 % regenerated (naive identical); pairs CSV differs. Magnitude 0.01 pp — immaterial to conclusions, cause not investigated (likely registry resynchronisation after generation). I restored the committed files. Also unchanged: README says 28 tests; actual 119.

## 8. Remaining unresolved
1. The provenance of the Markdown/PDF per-operator figures (22.30 %, rank 4) — needs the original run output or confirmation they were hand-edited.
2. `data/plans_grid/` is trackable now but untracked; needs `git add`.
3. Decide whether to refresh the paper, site and PDFs (§6).
4. C16.
5. Probe noise: re-probing moves operator MAPE by ~2.5 pp, so ±2–3 pp is the honest precision of any figure from this grid; no repeated-probe variance was measured.
