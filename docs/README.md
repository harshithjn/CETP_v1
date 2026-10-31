# CETP documentation

Everything documentary lives here. Numbers about the project come from
[`results/CANONICAL.pdf`](results/CANONICAL.pdf) (original five-machine dataset) and from the
regenerated per-operator results described in [`research/09_C10_investigation_and_fix.md`](research/09_C10_investigation_and_fix.md).

| Folder | What it holds |
|---|---|
| [`index.html`](index.html) | The project page. Self-contained; open in a browser or host as-is. |
| [`guides/`](guides) | `RUNNING` (install, verify, reproduce), `QUICKSTART`, `HANDBOOK` (concepts, findings, limits), `IMPLEMENTATION_REFERENCE` (what the code really does), `MODEL_CARD` (shipped artifacts). |
| [`design/`](design) | `LLD` (design document), `OPERATOR_MODEL` (the per-operator design and what each move is worth). |
| [`reference/`](reference) | `TASKS` (history and open items), `DATASHEET`, `PREREGISTRATION`, `RELATED_WORK`. |
| [`results/`](results) | `CANONICAL` plus one report per experiment (tiers 1–4, additions, cross-ISA, laptop-to-production, concurrency). |
| [`paper/`](paper) | `CETP_paper.tex` (draft, numbers updated to the regenerated grid results). |
| [`research/`](research) | The audited evidence pack: architecture, implementation, data and results, execution, history, gaps, method comparison, and the C10 fix. Start at `00_progress_index.md`. |

## Caveats
- The PDFs in `guides/`, `design/`, `reference/` and `results/` were rendered from Markdown that was
  removed from the working tree (`git show 38c4bf2^:<path>`); the Markdown is the only editable
  source. `make docs` / `make docs-all` therefore have nothing to build and should not be run.
- Some PDFs still quote superseded per-operator figures (24.95 % / 22.30 %); the regenerated value is
  22.41 %. See `research/09_C10_investigation_and_fix.md` §6.
- Deleted as superseded, one-off or duplicate (all recoverable from git): phase-7 report, phase-8 gate
  demo, DOC_CORRECTIONS, OVERNIGHT_REPORT, ROADMAP, CONFERENCE_PLAN, the generated INDEX and README PDFs,
  and the two README PDFs mirroring `data/` and `scripts/`.
