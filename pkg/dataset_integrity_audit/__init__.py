"""Reproducible integrity auditing for datasets."""
from .core import (  # noqa: F401
    NEAR_DUP_THRESHOLD, SHINGLE_SIZE, LSH_SWITCH_AT,
    all_pairs, classify_clusters, connected_components, curve,
    distributions, find_degenerate, find_exact_dupes, infer_schema,
    jaccard, leak, lsh_pairs, main, normalize_sql, normalize_text,
    prep, shingles, validate,
)

__version__ = "0.1.0"
