# bird-critic-audit

A reproducible integrity audit for Hugging Face datasets, and the reports it produces for the benchmarks people quote most.

Benchmarks get cited long before anyone checks whether their items are distinct. This is the check: one dependency-free script that validates the schema, flags degenerate records, and measures how much of a dataset is genuinely redundant.

**So far every benchmark measured has come out clean.** That is the finding, and it is reported as-is rather than dressed up into a scandal.

## Results

| Dataset | Records | Schema issues | Degenerate | Identical dupes | Identical % | Variant clusters | Groups | Largest group |
|---|---|---|---|---|---|---|---|---|
| BIRD-CRITIC 1.0 (open) | 600 | 0 | 0 | 2 | 0.33% | 3 | 15 | 13.8% |
| Spider (train) | 7000 | 0 | 0 | 16 | 0.23% | 69 | 140 | 2.4% |
| Spider (dev) | 1034 | 0 | 0 | 0 | 0.0% | 3 | 20 | 11.6% |

Full table in [`COMPARISON.md`](COMPARISON.md); per-dataset reports under [`prepared/`](prepared/).

## The distinction that matters

A naive text-similarity pass reports **77 duplicate clusters** in Spider's training split. Only **8** of them are real.

The other 69 share a question stem while differing in the reference SQL, the database, or both — one phrasing reused against different schemas, which is deliberate construction rather than redundancy. In BIRD-CRITIC the same pattern appears: 4 clusters flagged, 1 genuine. `PostgreSQL_215` and `PostgreSQL_216` ask an identical question, one filed under `Personalization` and one under `Efficiency`, with different `issue_sql`.

Reporting the raw cluster count overstates the problem by roughly **9x** on Spider and **4x** on BIRD-CRITIC. `audit.py` separates the two and reports both numbers, because only records identical across every evaluated field let a model bank the same answer twice.

The number worth carrying into any claim about generalisation is not duplication but concentration. BIRD-CRITIC draws 600 records from 15 databases, the largest supplying 13.8% of all items. Spider's training split is far more diverse at 140 databases and a 2.4% maximum. Both are design properties rather than errors, but they mean the two benchmarks reward schema-specific familiarity to very different degrees.

## Does the fast path agree with the slow one?

Above 3,000 records exact all-pairs comparison stops being practical, so the script switches to MinHash-LSH. That is a different algorithm, and swapping algorithms mid-table without checking is how comparisons quietly become meaningless.

Run on Spider's dev split, where both methods are feasible:

| Method | Candidate pairs examined | Pairs found | Clusters |
|---|---|---|---|
| exact all-pairs | 534,061 | 3 | 3 |
| MinHash-LSH | 232 | 3 | 3 |

Identical output from 0.04% of the work. LSH generates candidates only — every reported pair is then verified with its true Jaccard score, so similarity values mean the same thing under both methods.

Reproduce it with `--lsh-threshold 0`, which forces LSH on a dataset small enough to check by brute force.

## Usage

```bash
pip install datasets

python audit.py fetch --dataset xlangai/spider --split train --out spider-train.jsonl
python audit.py prep  --data spider-train.jsonl --preset spider --name "Spider (train)" --out-dir prepared/spider-train
python audit.py compare "prepared/*/summary.json" --out COMPARISON.md
```

Any dataset works, not just the presets — point the field roles at the right columns:

```bash
python audit.py prep --data mydata.jsonl \
  --text-field question --answer-field answer --group-field source \
  --label-fields difficulty,topic
```

Three roles drive everything: `text` is the natural-language side, `answer` the reference solution, `group` the schema or source an item is drawn from. Presets exist for `bird-critic` and `spider`.

For BIRD-CRITIC, note the split is named `open`, not `train` — `split='train'` raises `ValueError: Unknown split "train"`.

`audit.py` is pure standard library; `datasets` is needed only by `fetch`.

## What `prep` measures

1. **Schema** — infers the majority type of every field and flags records that deviate or omit it.
2. **Degenerate records** — empty text, answer, or group fields.
3. **Exact duplicates** — on normalized text, and separately on comment-stripped reference SQL.
4. **Near-duplicates** — 3-word-shingle Jaccard at a 0.70 threshold, exact below 3,000 records and MinHash-LSH above.
5. **Cluster classification** — union-find over near-duplicate pairs, then a split into *fully identical* and *shared-stem variant*.
6. **Concentration** — distinct groups, mean items per group, largest group share.

Outputs `summary.json` (machine-readable), `REPORT.md` (human-readable), and `prepared.jsonl` (normalized records with comparison keys attached; skip with `--no-prepared`).

## Limitations

LSH recall is not guaranteed, so counts on datasets above the switch threshold are **lower bounds**. The dev-split check above found no loss, but one clean result is not a proof. Every report states which method produced it.

Similarity is computed over the text field only. Reference SQL is compared exactly, after stripping comments and collapsing whitespace, because near-identical SQL is common across genuinely independent problems and would produce noise rather than signal.

**Cross-split contamination is not yet measured.** Overlap between a training split and its evaluation split is a more consequential problem than duplication within either one, and this tool does not currently look for it. That is the next thing to build.

The 0.70 threshold and 3-word shingle size are constants at the top of `audit.py`, exposed as `--threshold`. They are defensible defaults, not tuned optima.

## License

MIT
