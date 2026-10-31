# CETP Master Technical Reference (LaTeX)
Compile (TeX Live with pgfplots, tcolorbox, listings, longtable, cleveref, natbib, mathptmx):

    pdflatex main && bibtex main && pdflatex main && pdflatex main

Layout: `main.tex`, `preamble.tex`, `chapters/` (front matter, ch. 1-18, appendices A,C-J), `references.bib` (appendix K).
Diagrams are TikZ/pgfplots inside the chapter files (ch. 1, 4, 9, 10, 13); there are no separate diagram source files.
Appendix B (acronyms) is the Abbreviations list in the front matter.
See `VERIFICATION_REPORT.md` for what was and was not verified.

## Audit
`AUDIT_REPORT.md` is the independent audit of this document (also Appendix L of `main.pdf`). The audit changed the text; `chapters/` before the audit is not kept in this folder.
