# 07 — Questions and documentation readiness

## Prioritised questions (cannot be answered from the repo)
1. **Which method is "the project" for the final document?** README headline is Gen-1 (roofline, 5 EC2); README banner and paper abstract are Gen-2 (per-operator, 13 environments). Which does the examiner/reader see as the contribution, and is the paper (`docs/paper/CETP_paper.tex`, untracked) the target of the 20-page document?
2. **Chat history:** please export/paste the earlier Claude conversations (original requirements, why TPC-H/PostgreSQL, EC2 setup, the Oct 12–13 audit "Tier 0–4" and "Additions 1–7"), or the TASKS/LLD/HANDBOOK text. They were not accessible to me.
3. **Deleted Markdown:** is it intentional that all docs except README were deleted (`38c4bf2`)? May I read them from `git show 425a2c2:<path>` (LLD, HANDBOOK, TASKS, OVERNIGHT_REPORT, QUICKSTART, MODEL_CARD, results/*.md)?
4. **EC2 provenance:** instance types/regions, EBS volume types, PostgreSQL version and config, dates, whether raw logs still exist (needed for the Data section).
5. **Origin of 324 % vs 123 % and 23.38 %, 77.7 %→15.0 %, 54.5 %:** which script/file produced each, and on what subset (see 06-C3).
6. **Was `models/calibration.json` rebuilt after the warmup-discard fix?** (CANONICAL says all p95/p99 artifacts needed regeneration.)
7. **Course/submission context:** rubric, required sections, page limit, team roles (paper lists 5 authors), and which parts are your own implementation vs shared.
8. **Is the unfinished product layer (VS Code extension, ingest/telemetry) in scope** for what you must be able to explain, and may I read its uncommitted diff?
9. **Docker/PostgreSQL availability** on this machine: may I run `make verify-determinism`, `make artifact-verify`, or a probe against a local PG for a first-hand Gen-2 measurement?
10. Anything intentionally excluded (TPC-DS, JOB, plan-drift tiers) from the story.

## Readiness assessment
**Sufficient now:** architecture and code behavior of the installable package; Gen-1 headline (reproduced); test and selftest status; honest-limitations narrative (the repo is unusually candid); decision rationale as stated in code.
**Not sufficient yet:** (a) chronology/rationale beyond git subjects and comments; (b) the Gen-2 experimental pipeline — I have not read `operator_probe.py`, `container_grid.py`, `operator_model_evaluation.py`, or any PDF; (c) data provenance for EC2; (d) the ~15 numerical claims marked **C** in 03; (e) reconciled numbers (06 C3–C5).
**Verdict: partially ready.** I could write a rigorous *architecture + implementation + Gen-1 results* document today. For the full master bible, the highest-impact gaps, in order: Q1 (which headline), Q2/Q3 (history and deleted docs), reading the unread Gen-2 scripts and PDFs (I can do that next without your input), then Q4–Q6.
