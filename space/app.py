"""Dataset Integrity Auditor - Gradio front end for audit.py.

Paste any Hugging Face dataset id and get the duplication, degeneracy and
concentration report. Field roles are guessed when not supplied.
"""

import json
import traceback

import gradio as gr

import audit

# Auditing is O(n^2) below the LSH threshold and every run is synchronous, so
# large datasets are truncated rather than left to time the Space out.
MAX_RECORDS = 6000

TEXT_CANDIDATES = ["question", "prompt", "query", "instruction", "text",
                   "problem", "input", "issue"]
ANSWER_CANDIDATES = ["answer", "canonical_solution", "solution", "sql",
                     "query", "output", "issue_sql", "response", "code"]

EXAMPLES = [
    ["xlangai/spider", "validation", "", "question", "query"],
    ["openai/gsm8k", "test", "main", "question", "answer"],
    ["openai/openai_humaneval", "test", "", "prompt", "canonical_solution"],
    ["birdsql/bird-critic-1.0-open", "open", "", "query", "issue_sql"],
]


def guess_field(columns, candidates, exclude=None):
    for c in candidates:
        if c in columns and c != exclude:
            return c
    return None


def load_rows(dataset_id, split, config):
    from datasets import load_dataset, get_dataset_split_names

    kwargs = {"split": split}
    if config.strip():
        kwargs["name"] = config.strip()
    try:
        return load_dataset(dataset_id, **kwargs)
    except ValueError as exc:
        # The most common failure by far is a wrong split name - BIRD-CRITIC
        # calls its only split "open", not "train". Say so instead of raising.
        try:
            available = get_dataset_split_names(
                dataset_id, config.strip() or None)
            raise gr.Error(
                f"Split '{split}' not found. Available splits: "
                f"{', '.join(available)}") from exc
        except gr.Error:
            raise
        except Exception:
            raise gr.Error(str(exc)) from exc


def run(dataset_id, split, config, text_field, answer_field,
        progress=gr.Progress()):
    dataset_id = (dataset_id or "").strip()
    if not dataset_id:
        raise gr.Error("Enter a dataset id, for example xlangai/spider")

    progress(0.1, desc="Downloading")
    ds = load_rows(dataset_id, (split or "train").strip(), config or "")
    columns = list(ds.column_names)

    text_field = (text_field or "").strip() or guess_field(
        columns, TEXT_CANDIDATES)
    if not text_field:
        raise gr.Error(
            f"Could not guess the text field. Columns are: "
            f"{', '.join(columns)}. Name one in the Text field box.")
    answer_field = (answer_field or "").strip() or guess_field(
        columns, ANSWER_CANDIDATES, exclude=text_field)

    total = len(ds)
    truncated = total > MAX_RECORDS
    if truncated:
        ds = ds.select(range(MAX_RECORDS))

    progress(0.4, desc="Auditing")
    records = [dict(r) for r in ds]
    roles = {"text": text_field, "answer": answer_field,
             "group": guess_field(columns, ["db_id", "source", "category",
                                            "subject", "domain"]),
             "labels": []}

    schema = audit.infer_schema(records)
    schema_issues = audit.validate(records, schema)
    degenerate = audit.find_degenerate(records, roles)
    dup_text = audit.find_exact_dupes(
        records, lambda r: audit.normalize_text(
            audit.as_text(r.get(roles["text"]))))

    sigs = [audit.shingles(audit.as_text(r.get(roles["text"])))
            for r in records]
    n = len(records)
    if n > audit.LSH_SWITCH_AT:
        method = "MinHash-LSH"
        pairs, candidates = audit.lsh_pairs(
            sigs, audit.NEAR_DUP_THRESHOLD, audit.make_perms(audit.MINHASH_PERMS))
    else:
        method = "exact all-pairs"
        pairs = audit.all_pairs(sigs, audit.NEAR_DUP_THRESHOLD)
        candidates = n * (n - 1) // 2

    clusters = audit.connected_components(pairs, n)
    identical, variant = audit.classify_clusters(clusters, records, roles)
    dist = audit.distributions(records, roles)

    progress(0.9, desc="Writing report")
    n_identical = sum(c["size"] for c in identical)
    pct = lambda x: f"{100.0 * x / n:.2f}%" if n else "n/a"

    lines = [
        f"## {dataset_id} [{split}]", "",
        f"**{n} records audited**"
        + (f" (truncated from {total}; this Space caps at {MAX_RECORDS})"
           if truncated else ""),
        f"Fields: text=`{text_field}`"
        + (f", answer=`{answer_field}`" if answer_field else
           ", answer=*(none found - duplicate detection is text-only)*"),
        "",
        "| Check | Result |", "|---|---|",
        f"| Schema issues | {len(schema_issues)} |",
        f"| Degenerate records | {len(degenerate)} |",
        f"| Exact duplicate text groups | {len(dup_text)} |",
        f"| Near-duplicate clusters | {len(clusters)} |",
        f"| — genuinely identical | **{len(identical)}** "
        f"({n_identical} records, {pct(n_identical)}) |",
        f"| — shared-stem variants | {len(variant)} |",
    ]
    if dist.get("distinct_groups"):
        lines.append(
            f"| Distinct `{dist['group_field']}` values | "
            f"{dist['distinct_groups']} "
            f"(largest {100 * dist['top_group_share']:.1f}%) |")
    lines += ["", f"Search: {method} over {candidates} candidate pairs.", ""]

    if identical:
        lines += ["### Genuinely duplicated", ""]
        for c in identical[:10]:
            lines.append(f"- {', '.join(c['members'][:6])}")
        lines.append("")
    if variant:
        lines += [
            "### Shared-stem variants (usually deliberate)", "",
            f"{len(variant)} clusters share text but differ in the reference "
            "answer or grouping. A naive dedup pass would remove these too - "
            "which is the whole point of separating them.", "",
        ]
        for c in variant[:5]:
            lines.append(f"- {', '.join(c['members'][:6])}")
        lines.append("")

    if not identical and not degenerate and not schema_issues:
        lines += ["---", "",
                  "**Nothing structurally wrong found.** Reported as-is."]

    return "\n".join(lines), json.dumps(
        {"dataset": dataset_id, "split": split, "records": n,
         "schema_issues": len(schema_issues),
         "degenerate": len(degenerate),
         "identical_clusters": len(identical),
         "identical_records": n_identical,
         "variant_clusters": len(variant),
         "method": method}, indent=2)


def safe_run(*args, progress=gr.Progress()):
    try:
        return run(*args, progress=progress)
    except gr.Error:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise gr.Error(f"{type(exc).__name__}: {exc}") from exc


with gr.Blocks(title="Dataset Integrity Auditor") as demo:
    gr.Markdown(
        "# Dataset Integrity Auditor\n"
        "How much of a benchmark is actually redundant? Paste a Hugging Face "
        "dataset id.\n\n"
        "The point of this tool is one distinction: records that are "
        "**identical across every evaluated field** versus records that merely "
        "**share a question stem**. Naive similarity lumps them together and "
        "overstates duplication - by 9x on Spider's training split. Only the "
        "first kind lets a model bank the same answer twice.\n\n"
        "Code and full write-up: "
        "[github.com/ashishsinha1602/dataset-integrity-audit]"
        "(https://github.com/ashishsinha1602/dataset-integrity-audit)")

    with gr.Row():
        dataset_id = gr.Textbox(label="Dataset id", value="xlangai/spider",
                                scale=3)
        split = gr.Textbox(label="Split", value="validation", scale=1)
        config = gr.Textbox(label="Config (optional)", value="", scale=1)
    with gr.Row():
        text_field = gr.Textbox(label="Text field (blank = guess)", value="")
        answer_field = gr.Textbox(label="Answer field (blank = guess)", value="")

    go = gr.Button("Audit", variant="primary")
    report = gr.Markdown()
    raw = gr.Code(label="summary.json", language="json")

    gr.Examples(examples=EXAMPLES,
                inputs=[dataset_id, split, config, text_field, answer_field])

    go.click(safe_run,
             inputs=[dataset_id, split, config, text_field, answer_field],
             outputs=[report, raw])

if __name__ == "__main__":
    demo.launch()
