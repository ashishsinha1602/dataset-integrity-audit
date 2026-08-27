# Paper 2 — A Census of the Model Context Protocol Registry

`main.tex`. Every figure is produced from a full retrieval of the public MCP
registry (82,994 version records, 25,125 distinct servers, August 2026).

Reproduce with:

    python fetch_mcp_registry.py --out mcp-registry.jsonl
    python audit.py prep --data mcp-latest.jsonl --text-field description \
                         --answer-field name --group-field _status
    python audit.py curve --data mcp-latest.jsonl --text-field description \
                          --answer-field name

Build the PDF on https://overleaf.com (Upload Project) or with `pdflatex`.
Suggested arXiv categories: primary cs.SE, cross-list cs.AI.
