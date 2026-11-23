# Compiling the paper on Overleaf

Bundle: `paper_overleaf.zip` (repo root, untracked). Contents: `main.tex`, `references.bib`,
`figures/` (gdelt_daily_defect.pdf, decay.pdf, reliability.pdf - the only figures `main.tex`
includes; `horizon_profile.pdf` is not used), `tables/pledger.tex`.

## Steps
1. Overleaf -> New Project -> Upload Project -> choose `paper_overleaf.zip`.
2. Menu -> Main document: `main.tex`.
3. Menu -> Compiler: `pdfLaTeX` (TeX Live current default is fine).
4. Click Recompile. Overleaf runs LaTeX, BibTeX, LaTeX, LaTeX automatically, so citations and
   cross-references resolve in one click. If you see "?" for citations, recompile once more.

## Checklist
- Author block: all rows render, nothing overflows the column or runs into the abstract.
- Figures: three figures appear near their text, none pushed to the end; fonts embedded; captions readable.
- Tables: the p-value ledger fits the column width (no overfull box warnings in the log).
- References: every in-text cite resolves (no "?"); all 16 entries listed; the DOI fields print as expected.
  `leetaru2013` carries an UNCONFIRMED note in `references.bib`.
- Page count: report the actual count (target to be set by the owner); do not trim before then.
- Log: no "undefined reference" or "citation undefined" warnings.
