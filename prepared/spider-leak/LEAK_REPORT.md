# Cross-split contamination: Spider train -> Spider dev

Does anything in **Spider dev** already appear in **Spider train**? Overlap between a training split and the split used to score a model inflates that score directly, which makes it a more consequential defect than duplication inside either split.

## Inputs

- Spider train: **7000** records
- Spider dev: **1034** records
- `db_id` values: 140 vs 20, **0 shared**

## Findings

| Check | Matches | % of Spider dev |
|---|---|---|
| Identical text | 6 | 0.58% |
| Identical reference answer | 8 | 0.77% |
| **Both identical (answerable from memory)** | **2** | **0.19%** |
| Near-duplicate text (Jaccard >= 0.7) | 7 | 0.68% |

The two splits share **no** `db_id` values, so they are drawn from disjoint sources by construction. Any text similarity found here is phrasing reuse across different underlying data rather than the same problem appearing twice - a much weaker form of overlap, and often intentional.

## Method

Search: **exact-cross-product** over 7238000 candidate pairs. Exact checks compare normalized text and comment-stripped reference answers.
