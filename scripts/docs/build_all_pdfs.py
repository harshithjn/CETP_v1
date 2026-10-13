"""
Render every Markdown document in the repository to PDF under docs/.

LAYOUT. The tree is mirrored rather than flattened, because flattening collides (there are
three README.md files) and loses the origin. `results/CANONICAL.md` becomes
`docs/results/CANONICAL.pdf`; a root-level file becomes `docs/<name>.pdf`.

INCREMENTAL. A PDF is rebuilt only when its source is newer, because each render costs a
Chrome process. Pass --force to rebuild everything.

WHAT IS SKIPPED, and why:
  .pytest_cache/, node_modules/, data/external/   tooling and third-party, not ours
  docs/*.pdf sources already in docs/             rendered in place, not duplicated

The index is generated too, so docs/INDEX.md always lists what is actually there rather
than what someone remembered to add.

  python3 scripts/docs/build_all_pdfs.py            # only what changed
  python3 scripts/docs/build_all_pdfs.py --force
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
DOCS = PROJECT / "docs"
BUILD = PROJECT / "scripts" / "docs" / "build_pdf.py"

SKIP_DIRS = {".pytest_cache", "node_modules", ".git", "__pycache__",
             "data/external", "models_backup", "dist", "build"}

# One line per document, so the index is a map rather than a listing. Anything without an
# entry still appears -- with its first heading -- so a new document is never invisible.
BLURB = {
    "RUNNING.md": "**Start here to run it.** Install, verify, predict, reproduce, troubleshoot",
    "docs/HANDBOOK.md": "**Start here to understand it.** A-to-Z: concepts, findings, history, limits",
    "docs/LLD.md": "Low-Level Design. Architecture, the model, results, limitations",
    "README.md": "Repository overview and the headline result",
    "QUICKSTART.md": "Condensed usage for the installed `cetp` command",
    "OPERATOR_MODEL.md": "Design and status of the per-operator work; what each move is worth",
    "TASKS.md": "Full history: everything done, everything that failed, everything open",
    "DOC_CORRECTIONS.md": "Itemised errors in the superseded deck and LLD",
    "DATASHEET.md": "Datasheet for the dataset (Gebru et al. format)",
    "PREREGISTRATION.md": "What was committed to before the Tier-3 experiments ran",
    "RELATED_WORK.md": "Prior art, verified against publishers rather than quoted from memory",
    "ROADMAP.md": "Longer-term plan",
    "CONFERENCE_PLAN.md": "Publication plan and venue analysis",
    "IMPLEMENTATION_REFERENCE.md": "Implementation notes",
    "OVERNIGHT_REPORT.md": "Report from the unattended run of 2026-10-13",
    "TIER1_RESULTS.md": "Tier 1 summary — method",
    "TIER2_RESULTS.md": "Tier 2 summary — dataset",
    "TIER3_RESULTS.md": "Tier 3 summary — research questions",
    "models/MODEL_CARD.md": "What the shipped artifacts are, and are not",
    "results/CANONICAL.md": "**The source of truth** for the original five-machine dataset",
    "results/operator_model.md": "The capability grid and the ablations",
    "results/laptop_to_prod.md": "Laptop to eleven production machines",
    "results/cross_isa_prediction.md": "The cross-architecture failure and its repair",
    "results/cross_isa_operator_rates.md": "Why the failure is not operator mix",
    "results/laptop_to_ec2.md": "The 59.7% ceiling on scalar capability models",
    "results/concurrency.md": "Concurrency elasticity, fitted and cross-validated",
}


def should_skip(path):
    rel = path.relative_to(PROJECT).as_posix()
    return any(rel == d or rel.startswith(d + "/") or f"/{d}/" in f"/{rel}"
               for d in SKIP_DIRS)


def target_for(md):
    rel = md.relative_to(PROJECT)
    if rel.parts[0] == "docs":
        return DOCS / Path(*rel.parts[1:]).with_suffix(".pdf")
    return DOCS / rel.with_suffix(".pdf")


def first_heading(md):
    for line in md.read_text(errors="replace").split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return md.stem


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--index-only", action="store_true")
    args = ap.parse_args()

    mds = sorted(p for p in PROJECT.rglob("*.md")
                 if not should_skip(p) and p.name != "INDEX.md")
    print(f"{len(mds)} Markdown documents")

    built, skipped, failed = [], [], []
    if not args.index_only:
        for md in mds:
            out = target_for(md)
            out.parent.mkdir(parents=True, exist_ok=True)
            if (not args.force and out.exists()
                    and out.stat().st_mtime >= md.stat().st_mtime):
                skipped.append(md)
                continue
            r = subprocess.run([sys.executable, str(BUILD), str(md), "-o", str(out)],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0 or not out.exists():
                failed.append((md, (r.stderr or r.stdout).strip()[-200:]))
                print(f"  FAIL {md.relative_to(PROJECT)}")
            else:
                built.append(md)
                print(f"  ok   {out.relative_to(PROJECT)}")

    # ---- index -------------------------------------------------------------
    groups = {}
    for md in mds:
        rel = md.relative_to(PROJECT)
        key = "Results (generated — never hand-edited)" if rel.parts[0] == "results" \
            else "Start here" if rel.as_posix() in ("RUNNING.md", "docs/HANDBOOK.md",
                                                     "docs/LLD.md", "README.md") \
            else "Reference"
        groups.setdefault(key, []).append(md)

    lines = [
        "# Documentation index",
        "",
        "Every Markdown document in this repository, rendered to PDF under `docs/`.",
        "The tree is mirrored, so `results/CANONICAL.md` is at `docs/results/CANONICAL.pdf`.",
        "",
        "Regenerate with `make docs-all`. Sources are the Markdown files; **the PDFs are",
        "build output and should not be edited.**",
        "",
    ]
    for key in ("Start here", "Results (generated — never hand-edited)", "Reference"):
        if key not in groups:
            continue
        lines += [f"## {key}", "", "| Document | PDF | About |", "|---|---|---|"]
        for md in sorted(groups[key],
                         key=lambda m: (m.relative_to(PROJECT).as_posix() != "RUNNING.md",
                                        m.relative_to(PROJECT).as_posix())):
            rel = md.relative_to(PROJECT).as_posix()
            pdf = target_for(md).relative_to(DOCS).as_posix()
            about = BLURB.get(rel) or first_heading(md)
            lines.append(f"| `{rel}` | [{pdf}]({pdf}) | {about} |")
        lines.append("")
    (DOCS / "INDEX.md").write_text("\n".join(lines) + "\n")

    idx_pdf = DOCS / "INDEX.pdf"
    subprocess.run([sys.executable, str(BUILD), str(DOCS / "INDEX.md"),
                    "-o", str(idx_pdf)], capture_output=True, text=True, timeout=300)

    print(f"\nbuilt {len(built)}, up to date {len(skipped)}, failed {len(failed)}")
    for md, err in failed:
        print(f"  {md.relative_to(PROJECT)}: {err}")
    print(f"index: {(DOCS / 'INDEX.md').relative_to(PROJECT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
