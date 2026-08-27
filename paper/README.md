# Paper

`main.tex` — "Redundancy Is Not Duplication: Integrity Audits of Seven
Benchmark and Instruction Datasets"

Every number in the paper is produced by `audit.py` in the repository root and
reproduces with the commands in the Reproducibility section.

## Building

No LaTeX toolchain is required locally. Either:

- Upload `main.tex` to https://overleaf.com (New Project -> Upload Project),
  which compiles it in the browser, or
- `pdflatex main.tex` twice if you have TeX Live or MiKTeX installed.

Standard `article` class, no custom packages beyond `booktabs`, `amsmath`,
`hyperref`, `graphicx`, `geometry`, `url` — all present in any TeX
distribution and on arXiv.

## Submitting to arXiv

arXiv requires endorsement for first-time submitters in `cs.CL` and `cs.LG`.
Suggested categories: primary `cs.CL`, cross-list `cs.LG`.

Once the paper is on arXiv it is indexed automatically by
https://huggingface.co/papers, where it can be surfaced and discussed.
