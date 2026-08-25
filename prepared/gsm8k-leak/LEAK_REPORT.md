# Cross-split contamination: GSM8K train -> GSM8K test

Does anything in **GSM8K test** already appear in **GSM8K train**? Overlap between a training split and the split used to score a model inflates that score directly, which makes it a more consequential defect than duplication inside either split.

## Inputs

- GSM8K train: **7473** records
- GSM8K test: **1319** records

## Findings

| Check | Matches | % of GSM8K test |
|---|---|---|
| Identical text | 0 | 0.0% |
| Identical reference answer | 0 | 0.0% |
| **Both identical (answerable from memory)** | **0** | **0.0%** |
| Near-duplicate text (Jaccard >= 0.7) | 0 | 0.0% |

## Method

Search: **minhash-lsh** over 5 candidate pairs. Exact checks compare normalized text and comment-stripped reference answers.

Both splits are bucketed together and only cross-split pairs kept; every candidate is verified with its true Jaccard score. Recall is not guaranteed, so these counts are lower bounds.
