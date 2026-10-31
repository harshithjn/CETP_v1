# CETP Master Bible — Independent Audit Report

Audited 2026-10-27 against repo `x86-probe` @ 38c4bf2 (working tree modified). Evidence tags: RUN executed; CODE read, not executed; FILE read in committed result file; PACK from evidence pack; UNK not established. No critical findings. All findings below are applied to the corrected LaTeX source unless Status says otherwise (Appendix L of the PDF has the same table and the lists below).

| ID | Severity | Section | Claim | Evidence | Problem | Correction | Status |
|---|---|---|---|---|---|---|---|
| A-01 | High | Ch.18 | "ties or loses in four of seven same-ISA cells" | Per-cell results: four cells lose to no-change, one ties, two 1-CPU cells win [FILE] | Overstated ties; the method loses outright in four cells | Named the four losing cells, one tie, two wins | Corrected, checked against result files |
| A-02 | Medium | Ch.13 | Learned model "worst" with machines unseen | LOMO: learned 23.90, analytical 21.61, no-change 31.96 [FILE] | No-change is worse (31.96) | "worst of the five non-trivial models" | Corrected |
| A-03 | High | Ch.13 | "Differences between methods are small in every row"; per-machine table without no-change column | Per-machine MAPE incl. no-change: c7i 23.71 beats all fitted models; learned is 9--12pp worse on c5a, c7i [FILE] | Claim false for learned; the pooled 21.61 hides c7i | Added no-change column and rewrote sentence | Corrected |
| A-04 | Medium | Ch.13 | p50 "unaffected in all 105" cells | Maximum inflation 1.007× [FILE] | Inflated by up to 0.7%, not unaffected | Reworded with the bound | Corrected |
| A-05 | Low | Ch.13 | Naive baseline 23.03 vs canonical 23.05; 21.60 vs 21.61 | Two files use slightly different baselines | Looked like a contradiction | Stated both and why | Corrected |
| A-06 | Medium | Ch.12 | Leakage "did not flatter the headline" | LOQO 17.04 vs 17.29; LOMQO 26.56 vs 26.86: leaky is marginally better [FILE] | True for LOMO only | Scoped to LOMO; added the others | Corrected |
| A-07 | High | Ch.8 | Eq. used an undefined κ_c | `operator_model.py` [CODE] | Symbol never defined; unspecified-operator term misrepresented | Rewrote equation with κ_{∅} and defined it | Corrected |
| A-08 | Medium | Ch.8 | Eq. presented as the shipped gate | `gate/cetp_gate.py` uses classifier probabilities, own formula copy, pickled reference table [CODE] | Equation is the uniform-weight variant | Added explicit paragraph | Corrected |
| A-09 | Medium | Ch.4 | Component figure arrows and caption | Import statements: no `predict`→gate edge; `regress` uses `signature` [CODE] | Invented edge; wrong source node; arrows read as imports | Removed edge, rewired, caption now states data flow and lists the real imports | Corrected |
| A-10 | Medium | Ch.4 | "both CI workflows call Gen-1 only" | Three workflows exist; one runs the Gen-2 probe [CODE] | Wrong count | Named all three | Corrected |
| A-11 | Medium | Ch.7 | ingest: "truncated JSON is dropped and recorded" | `_balanced_json` returns `None`; loop continues silently [CODE] | Not recorded | Described the silent skip | Corrected; behaviour is a defect (X15) |
| A-12 | Low | Ch.7 | ingest return type, `query_id`, string-awareness worry | Returns `(entries, dropped)`; scanner tracks quotes and escapes [CODE] | Docstring and worry inaccurate | Corrected both | Corrected |
| A-13 | Low | Ch.7 | Output fields; row-ratio default; hardware term 1.0 scope; gate reference table | `operator_model.py`, `predict.py`, gate [CODE] | Incomplete or too narrow | Completed | Corrected |
| A-14 | High | Ch.11/14 | Installed `cetp` works as documented | Wheel built and installed in a clean venv: `selftest` "missing fixture"; default `predict-prod` raises `FileNotFoundError` [RUN] | Only source or editable installs work | Documented; added to defects X11 | Corrected; defect remains in product |
| A-15 | Medium | ch:interfaces | Single SLA semantics assumed | `cli.py`, gate [CODE] | Three different PASS/BLOCK rules | Added comparison | Corrected |
| A-16 | Medium | Ch.11/15 | CI regression workflow described generically | Hard-coded `--dev c7i --prod c5a`; runs PR SQL under `EXPLAIN ANALYZE` [CODE] | Machine labels meaningless; statements are executed | Documented; risk added | Corrected; defect remains (X13) |
| A-17 | High | Ch.9/14 | Suggested re-run with `--actual-ms` bootstraps calibration | `predict_prod.py`: refusal returns 4 before the record block [CODE] | Cannot bootstrap on an empty store | Documented; X12 | Corrected; not executed; defect remains |
| A-18 | Medium | Ch.14 | Install steps; `pip install -e .` "inferred"; dependency list | Workflow uses it [CODE]; `psycopg2-binary` only in `research` extra | Mislabelled; missing dependency | Fixed label; added requirement and caveat | Corrected |
| A-19 | Medium | App.D | "No environment variables are read" | `CETP_CALIBRATION_PATH`, `CETP_TELEMETRY_PATH` [CODE] | False | Listed both with defaults | Corrected |
| A-20 | Medium | Front matter, Ch.6 | PDFs "not opened"; docs deleted; Makefile cannot build docs | Seven PDFs text-extracted in audit; docs reorganised in working tree; 14 of 17 commits dated 2026-10-13 [RUN] | Description of repository state inexact | Rewritten with HEAD vs working-tree distinction | Corrected |
| A-21 | Medium | Ch.17/App.I | Stale numbers "in the PDFs"; 5 vs 7 machines attributed to "documents" | HANDBOOK, LLD show 24.95/24.58; laptop_to_prod PDF 132 pairs, 28.54 vs 38.22; CANONICAL calls 0.562 wrong [RUN] | Vague; and the 7-machine documents include the Review 2 deck and LLD made earlier in this engagement | Located each stale value; named the earlier deliverables | Corrected; those two deliverables are not regenerated |
| A-22 | Low | Ch.17 | Limitations list | Comment in `signature.py`: bandwidth probe source no longer exists [CODE] | Missing limitation | Added L11 | Corrected |
| A-23 | Low | Ch.1 | "unusually reproducible" | Byte-identical outcome is from the pack; not re-run | Overclaim | Reworded to design intent plus pack-reported outcome | Corrected |
| A-24 | Low | Ch.15 | Security table | See A-16 | Omitted CI execution risk | Added row | Corrected |
| A-25 | Low | Appendices | Lettering off by one; manual "Appendix D" | Table of contents | No Appendix B file; hard-coded reference | Added Appendix B; cross-reference now automatic; audit is Appendix L | Corrected |
| A-26 | Low | Bibliography | Missing years; Moerkotte entry type | `references.bib` | Incomplete entries | Entry type fixed; undated web sources left undated rather than guessed | Corrected; years unverified |
| A-27 | Low | App.G | FR-9 "Met" | Follows from A-11 | Overstated | Changed to Partial | Corrected |


## Missing topics


- Behaviour under a non-default PostgreSQL configuration (`work_mem`, `jit`, parallel settings) is not analysed; only the probe's `assert_comparable` guard is described.
- JOB and TPC-DS collection code exists but no results from it were found or documented.
- The VS Code extension is described only at interface level; its behaviour was not run.
- Licensing of vendored third-party data is not established ([UNK]).



## Contradictions between chapters (found and resolved)


- Conclusion vs results on the learned model and on the same-ISA cells (A-01, A-02, A-03).
- Architecture figure vs implementation chapter on which modules call which (A-09).
- Configuration appendix vs setup chapter on environment variables (A-19).
- Setup chapter's calibration recipe vs workflows chapter's description of the refusal path (A-17).



## Unsupported or only partly supported claims that remain


- Byte-identical regeneration and the 119-test count are taken from the pack and not re-run ([PACK]).
- `predict_prod.py` refusal-before-record defect is read from code, not executed ([CODE]).
- Statements about EC2 instance behaviour rest on the pack because the raw logs no longer exist ([PACK]).
- The six literature entries checked by web search were checked for existence only, not for content.
- Bibliography years for undated web sources are absent, not guessed.



## Important implementation details previously undocumented

Now documented: the two environment variables; the gate's divergent formula and its lack of equal-range guard; silent skipping of truncated log entries; the three SLA rules; the hard-coded machine labels and statement execution in the regression workflow; the installed-package failure; and the lost bandwidth-probe source.


## Questions the project owner must answer


- Data collection is dated January--April 2026 in the team's notes, but `signature.py` says August 2026 and git begins 2026-08-17. Which is right?
- Will the documentation reorganisation be committed, and which document set is canonical?
- Is the refusal-before-record order in `predict_prod.py` intended?
- Are `c7i` and `c5a` in the regression workflow intended?
- Can the STREAM-style bandwidth probe source be recovered?
- Which headline narrative is the submission: Gen-1 canonical or Gen-2 laptop-to-prod?
- Do the EC2 logs exist anywhere?
- Was `models/calibration.json` regenerated after the warm-up fix (R10)?
- Should the Review 2 deck and LLD (7 machines, F1 0.562) be regenerated from canonical values, and the Review 3 deck's 21.60/23.03 and its gate-tier figures reconciled?



## Compilation and reference check

Final build: `pdflatex`, `bibtex`, `pdflatex` twice. Result: 81 pages; 0 LaTeX errors; 0 undefined citations or references; 0 multiply defined labels; `bibtex` reports 3 warnings (empty year for the three undated web sources, left undated deliberately); 17 overfull boxes remain, none in the audit table, the largest (99pt) in the schema listing of app:schemas. The page count is well above the original 20-page target. The changed pages (architecture figure, results table, audit table) were rendered and inspected; the remaining pages were not re-inspected after this pass.

