# bird-critic-audit

A reproducible integrity audit of [BIRD-CRITIC 1.0 (open)](https://huggingface.co/datasets/birdsql/bird-critic-1.0-open), the 600-item SQL debugging benchmark.

Benchmarks get quoted long before anyone checks whether their items are distinct. This repo is the check: one dependency-free script that validates the schema, normalizes every record, and measures duplication, cross-dialect restatement, and database reuse.

**The result is that BIRD-CRITIC holds up.** That is the finding, and it is reported as-is rather than dressed up into a problem.

## Findings

| Check | Result |
|---|---|
| Records | 600 |
| Schema violations | 0 |
| Degenerate records (empty query / `issue_sql` / `db_id`) | 0 |
| Malformed JSON lines | 0 |
| Fully identical duplicate pairs | **1** (2 records, 0.3%) |
| Shared-stem variant clusters | 3 |
| Near-duplicate pairs spanning two dialects | 0 |
| Distinct databases | 15 (40 items each on average) |

The single genuine duplicate is `SQLServer_70` / `SQLServer_71` — same question, same `issue_sql`, same category, same database.

The other three flagged clusters (`PostgreSQL_215/216`, `262/263`, `265/266`) share a question stem but differ in `issue_sql` or `category`. `PostgreSQL_215` and `216` ask the same thing under `Personalization` and `Efficiency` respectively. That is deliberate construction, not redundancy, and a plain text-similarity pass that lumps them in with the real duplicate overstates the problem by 4x.

The number worth carrying forward is not duplication but concentration: 600 records drawn from 15 databases, the largest supplying 13.8% of all items. That rewards schema-specific familiarity and is worth stating whenever a BIRD-CRITIC score is used to argue for general SQL debugging ability.

Full output in [`prepared/REPORT.md`](prepared/REPORT.md) and [`prepared/summary.json`](prepared/summary.json).

## Reproducing

```bash
pip install datasets
python -c "from datasets import load_dataset; load_dataset('birdsql/bird-critic-1.0-open', split='open').to_json('bird-critic-open.jsonl')"
python audit.py prep --data bird-critic-open.jsonl
```

Note the split is named `open`, not `train` — `split='train'` raises `ValueError: Unknown split "train"`.

`audit.py` itself is pure standard library; `datasets` is needed only to pull the source data.

## What `prep` does

1. **Loads** the jsonl export, recording any malformed lines rather than crashing.
2. **Validates** all ten fields of every record against their expected types.
3. **Flags** degenerate records that cannot support an evaluation.
4. **Detects duplicates** three ways: exact on normalized `query`, exact on comment-stripped `issue_sql`, and near-duplicate by 3-word-shingle Jaccard at a 0.70 threshold, all-pairs.
5. **Clusters** near-duplicate pairs with union-find, then splits those clusters into *fully identical* and *shared-stem variant*.
6. **Writes** `prepared/prepared.jsonl` (normalized records with comparison keys attached), `prepared/summary.json`, and `prepared/REPORT.md`.

## Method notes

The near-duplicate threshold (0.70) and shingle size (3) are constants at the top of `audit.py`. All-pairs comparison is O(n²), which is 180k comparisons at n=600 and runs in a couple of seconds; it would need rethinking above roughly 10k records.

Similarity is computed over `query` only. `issue_sql` is compared exactly, after stripping comments and collapsing whitespace, because near-identical SQL is common and expected across independent problems and would produce noise rather than signal.

## License

MIT
