# Benchmark integrity comparison

Every column produced by the same `audit.py prep` pass. "Identical" counts records duplicated across every evaluated field; shared-stem variants are listed separately because they are usually deliberate.

| Dataset | Records | Schema issues | Degenerate | Identical dupes | Identical % | Variant clusters | Groups | Largest group |
|---|---|---|---|---|---|---|---|---|
| BIRD-CRITIC 1.0 (open) | 600 | 0 | 0 | 2 | 0.33% | 3 | 15 | 13.8% |
| Spider (train) | 7000 | 0 | 0 | 16 | 0.23% | 69 | 140 | 2.4% |
| Spider (dev) | 1034 | 0 | 0 | 0 | 0.0% | 3 | 20 | 11.6% |

## Search method per dataset

| Dataset | Method |
|---|---|
| BIRD-CRITIC 1.0 (open) | exact-all-pairs |
| Spider (train) | minhash-lsh |
| Spider (dev) | exact-all-pairs |

MinHash-LSH rows are lower bounds: candidates are verified exactly, but recall is not guaranteed.
