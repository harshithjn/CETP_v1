# 05 — History and decision log

**Evidence limits:** only git subjects/dates, code comments and the README were read. Prior chats, PDFs, and the deleted Markdown (LLD, HANDBOOK, TASKS, OVERNIGHT_REPORT, …) were not. Motivations below are what the code comments state, not inferred intent.

## Timeline from git [OBS]
- 2026-08-17: `366c5d4` Version 1, `6cd06fa` Version 2, `84c5624` ignore `tpch_data`. (EC2 collection described as "August 2026" in `signature.py`.)
- 2026-10-12/13 (14 commits in ≈2 days): buffer-bug correction, multi-core compute fix, model retrain/pinning, CI fixes (sklearn pin, Python 3.13, BLOCK-is-not-failure), per-operator model + x86 probe (`26649a1`, `aa20181`), cross-ISA measurement (`ece60ff`), product loop: ingest/calibration store/cross-arch refusal (`6197abf`), VS Code panel + site (`7c9fbdb`), docs/telemetry/version compat (`425a2c2`), RUNNING.md + site rebuild (`9251a6b`), all docs → PDF (`bf7fe5f`), **delete all Markdown except README** (`38c4bf2`).
- Uncommitted: `docs/paper/CETP_paper.tex` (new), site and VS Code extension edits, `models/hardware_signatures.json` (newline only).
- File mtimes show the repository root last touched 2026-10-13 21:43.

## Decision log
| # | Decision | Rationale stated in code | Evidence | Alternatives (stated) | Conf. |
|---|---|---|---|---|---|
| D1 | Ship analytical formula + `calibration.json`, not pickles, in `cetp` | pickles are sklearn-version-coupled; learned model only interpolates (17.29→26.86 %); formula protocol-invariant | `predict.py` docstring, `pyproject.toml` | learned GBR (23.90 % LOMO) | High (stated), numbers C |
| D2 | Default weights uniform `{.5,.25,.25}` instead of classifier probabilities | Tier 1: uniform 21.55 % vs classifier 21.61 % | `predict.py:67` | RF bottleneck classifier; measured elasticity (21.76 %) | Med |
| D3 | Compute signal = multi-core aggregate GFLOPS | single-core had wrong sign (+0.213 vs −0.691) | CANONICAL, PROVENANCE | single-core | High (V) |
| D4 | Root-node buffers only | summing the tree inflated data 3.06–12× | `predict.py:40` | tree sum (original, buggy) | High |
| D5 | Discard warmup rep (cold cache) | rep 1 slowest in 61/105 cells | CANONICAL §6 | keep all 20 | High (V) |
| D6 | Regression mode is the "releasable" product, not absolute SLA | 54.5 % false-alarm BLOCKs; hardware term cancels in ratio | `regress.py`, README | absolute gating, conformal gate (can't BLOCK; intervals too wide) | High on logic |
| D7 | Replace C proxy signature with in-SQL operator probes | cross-ISA 324 % vs 123 %; r=+0.958 between axes; FP vector irrelevant to TPC-H; works on managed PG | `operator_probe.py` header, README banner | C STREAM/GFLOPS probe | Med (Gen-2 evidence limited to grid + 11 cells) |
| D8 | Weighted ratio, not sum of scaled node times | parallel node times are CPU-averaged (2.85× wall on q19) | `operator_model.py` docstring | Σ scaled self-times | High (reasoned) |
| D9 | Key parallel correction on `under_gather`, not `Parallel Aware` | corrected 46 % of q19 vs 100 %; 1.49× vs true 3.01× | `plan_nodes.py:143-152` | `Parallel Aware` | High (stated measurement) |
| D10 | Take plan from production, force on dev (pg_hint_plan) | plan divergence = 70.5 % of cross-env difference when plan changes | `cross_env.py` docstring | model plan drift | Med; **untested in grid** |
| D11 | Few-shot calibration by **median**, `spread` selection, `MIN_OBSERVATIONS=3` | k=1 random worse than none (19.51 vs 15.84 %) | `operator_model.py`, `calibration_store.py` | mean; random selection | Med |
| D12 | Refuse uncalibrated cross-ISA prediction | 3 measurements worse than naive | `cross_env.py:63` | warn only | High |
| D13 | Per-pair calibration, no pooling across machines | correction absorbs machine-specific scale error | `calibration_store.py` docstring | pooled prior | Med |
| D14 | Gate workflow: BLOCK exit(1) must not fail CI; exit ≥2 does | retrained models caused spurious "failure" | `cetp-gate.yml` | loosen SLA | High |
| D15 | Delete all Markdown; keep PDFs generated from Markdown | unknown — docs INDEX still says PDFs are built *from* Markdown that no longer exists | `38c4bf2`, `docs/INDEX.md` | — | Low (see 06-C6) |
| D16 | Remove `dashboard/` | showed superseded numbers | README | — | High |

## Plan vs implementation / unfinished (stated by authors)
- Gate artifacts: README Limitations says shipped gate still carries single-core scores; code shows otherwise (06-C1).
- Cardinality term unvalidated; plan-forcing (move 3) not evaluable on data collected; concurrency not predictable from plan.
- Cross-ISA: capability scale does not transfer; calibrated only reaches parity with naive (23.38 vs 21.41 %).
- p95/p99 results pre-2026-10-12 contaminated; calibration table "must be regenerated" (CANONICAL) — status of regeneration not established.
- TASKS.pdf (open items list) not read.
