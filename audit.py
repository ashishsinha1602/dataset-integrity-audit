"""audit.py - reproducible integrity audit for any Hugging Face dataset.

    python audit.py fetch --dataset xlangai/spider --split train --out spider.jsonl
    python audit.py prep  --data spider.jsonl --preset spider --out-dir prepared/spider
    python audit.py compare prepared/*/summary.json --out COMPARISON.md

`prep` validates the schema, flags degenerate records, and measures how much
of a dataset is redundant: exact duplicates on the natural-language field,
exact duplicates on the reference SQL/answer field, and near-duplicates by
shingle Jaccard.

Near-duplicate search is exact all-pairs on small datasets and MinHash-LSH
candidate generation above --lsh-threshold records. LSH only proposes
candidates; every reported pair is verified with the true Jaccard score, so
the similarity numbers mean the same thing under both methods. Recall is not
guaranteed under LSH, and the method actually used is recorded in the summary
and printed in the report.

Standard library only, except `datasets` for `fetch`.
"""

import argparse
import glob
import json
import re
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path

NEAR_DUP_THRESHOLD = 0.70
SHINGLE_SIZE = 3

# Above this many records, switch from exact all-pairs to MinHash-LSH.
LSH_SWITCH_AT = 3000
MINHASH_PERMS = 128
LSH_BANDS = 32          # 32 bands x 4 rows: tuned for recall at t=0.70,
LSH_ROWS = 4            # since candidates are exactly verified afterwards.
_MERSENNE = (1 << 61) - 1

# Field roles per dataset. `text` is the natural-language side, `answer` the
# reference SQL or solution, `group` the schema/source the item is drawn from.
PRESETS = {
    "bird-critic": {
        "text": "query", "answer": "issue_sql", "group": "db_id",
        "labels": ["dialect", "category"],
    },
    "spider": {
        "text": "question", "answer": "query", "group": "db_id",
        "labels": [],
    },
    "generic": {
        "text": "question", "answer": None, "group": None, "labels": [],
    },
}

_WS = re.compile(r"\s+")
_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def normalize_text(s):
    return _WS.sub(" ", (s or "").strip().lower())


def normalize_sql(s):
    return _WS.sub(" ", _SQL_COMMENT.sub(" ", (s or "")).strip().lower())


def as_text(value):
    """Coerce a field to a single string; datasets use both str and list."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(as_text(v) for v in value)
    return str(value)


def shingles(text, n=SHINGLE_SIZE):
    words = normalize_text(text).split()
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


# --- fetch -----------------------------------------------------------------

def fetch(args):
    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit("error: pip install datasets")
    kwargs = {"split": args.split}
    if args.config:
        kwargs["name"] = args.config
    ds = load_dataset(args.dataset, **kwargs)
    print(f"{args.dataset} [{args.split}]: {len(ds)} rows, "
          f"fields {ds.column_names}")
    ds.to_json(args.out)
    print(f"wrote {args.out}")


# --- schema ----------------------------------------------------------------

def infer_schema(records):
    """Majority type per field, plus how often each field is present."""
    types, presence = defaultdict(Counter), Counter()
    for r in records:
        for k, v in r.items():
            presence[k] += 1
            types[k][type(v).__name__] += 1
    return {k: {"type": types[k].most_common(1)[0][0],
                "present": presence[k],
                "type_counts": dict(types[k])} for k in presence}


def validate(records, schema):
    """Flag missing fields and records deviating from the majority type."""
    issues = []
    total = len(records)
    for i, r in enumerate(records):
        for field, info in schema.items():
            if info["present"] < total and field not in r:
                issues.append({"index": i, "problem": "missing_field",
                               "field": field})
            elif field in r and type(r[field]).__name__ != info["type"]:
                issues.append({"index": i, "problem": "inconsistent_type",
                               "field": field, "majority": info["type"],
                               "got": type(r[field]).__name__})
    return issues


def find_degenerate(records, roles):
    out = []
    for i, r in enumerate(records):
        reasons = []
        if not as_text(r.get(roles["text"])).strip():
            reasons.append("empty_text")
        if roles["answer"] and not as_text(r.get(roles["answer"])).strip():
            reasons.append("empty_answer")
        if roles["group"] and not as_text(r.get(roles["group"])).strip():
            reasons.append("empty_group")
        if reasons:
            out.append({"index": i, "id": record_id(r, i), "reasons": reasons})
    return out


def record_id(r, i):
    for key in ("instance_id", "id", "task_id", "_id"):
        if r.get(key) is not None:
            return str(r[key])
    return f"row_{i}"


# --- duplicates ------------------------------------------------------------

def find_exact_dupes(records, key_fn):
    buckets = defaultdict(list)
    for i, r in enumerate(records):
        k = key_fn(r)
        if k:
            buckets[k].append(i)
    return {k: v for k, v in buckets.items() if len(v) > 1}


def all_pairs(sigs, threshold):
    pairs = []
    for i in range(len(sigs)):
        si = sigs[i]
        if not si:
            continue
        for j in range(i + 1, len(sigs)):
            score = jaccard(si, sigs[j])
            if score >= threshold:
                pairs.append((i, j, score))
    return pairs


def _hash_shingle(s):
    # zlib.crc32, not hash(): Python randomizes string hashing per process,
    # which would make LSH bucketing differ between runs.
    return zlib.crc32(s.encode("utf-8")) & 0xFFFFFFFF


def minhash_signature(shingle_set, perms):
    if not shingle_set:
        return None
    hashed = [_hash_shingle(s) for s in shingle_set]
    return [min(((a * h + b) % _MERSENNE) for h in hashed) for a, b in perms]


def lsh_pairs(sigs, threshold, perms):
    """MinHash-LSH candidate generation, then exact Jaccard verification."""
    signatures = {}
    for i, s in enumerate(sigs):
        sig = minhash_signature(s, perms)
        if sig is not None:
            signatures[i] = sig

    candidates = set()
    for band in range(LSH_BANDS):
        lo, hi = band * LSH_ROWS, (band + 1) * LSH_ROWS
        buckets = defaultdict(list)
        for i, sig in signatures.items():
            buckets[tuple(sig[lo:hi])].append(i)
        for members in buckets.values():
            if len(members) < 2:
                continue
            # A pathological bucket would blow up quadratically; cap it.
            if len(members) > 200:
                members = members[:200]
            for x in range(len(members)):
                for y in range(x + 1, len(members)):
                    candidates.add((members[x], members[y]))

    pairs = []
    for i, j in candidates:
        score = jaccard(sigs[i], sigs[j])
        if score >= threshold:
            pairs.append((i, j, score))
    return pairs, len(candidates)


def make_perms(n, seed=20260824):
    """Deterministic (a, b) coefficients. Fixed seed keeps runs reproducible."""
    state = seed
    perms = []
    for _ in range(n):
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        a = (state >> 17) % (_MERSENNE - 1) + 1
        state = (state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        b = (state >> 17) % _MERSENNE
        perms.append((a, b))
    return perms


def connected_components(pairs, n):
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _ in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return [g for g in groups.values() if len(g) > 1]


def classify_clusters(clusters, records, roles):
    """Separate true duplicates from variants sharing only a question stem.

    Records sharing a stem are not automatically a defect: a dataset may pair
    one question with different reference answers or categories on purpose.
    Only records identical across every evaluated field are redundant.
    """
    fields = [f for f in (roles["text"], roles["answer"], roles["group"])
              if f] + list(roles["labels"])
    identical, variant = [], []
    for c in clusters:
        first = records[c[0]]
        same = all(all(records[m].get(f) == first.get(f) for f in fields)
                   for m in c[1:])
        entry = {"members": [record_id(records[m], m) for m in c],
                 "size": len(c)}
        (identical if same else variant).append(entry)
    return identical, variant


def distributions(records, roles):
    out = {}
    if roles["group"]:
        by_group = Counter(as_text(r.get(roles["group"])) for r in records)
        out["group_field"] = roles["group"]
        out["distinct_groups"] = len(by_group)
        out["items_per_group_mean"] = round(len(records) / max(len(by_group), 1), 2)
        out["top_group_share"] = round(
            by_group.most_common(1)[0][1] / len(records), 4) if by_group else 0.0
        out["groups"] = dict(by_group.most_common(20))
    for label in roles["labels"]:
        out[label] = dict(Counter(as_text(r.get(label))
                                  for r in records).most_common())
    return out


# --- prep ------------------------------------------------------------------

def load(path):
    records, malformed = [], []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                malformed.append(lineno)
    return records, malformed


def resolve_roles(args, records):
    roles = dict(PRESETS[args.preset])
    if args.text_field:
        roles["text"] = args.text_field
    if args.answer_field:
        roles["answer"] = args.answer_field
    if args.group_field:
        roles["group"] = args.group_field
    if args.label_fields:
        roles["labels"] = [f for f in args.label_fields.split(",") if f]
    present = set().union(*(r.keys() for r in records)) if records else set()
    if roles["text"] not in present:
        sys.exit(f"error: text field '{roles['text']}' not in data. "
                 f"Available: {sorted(present)}. Pass --text-field.")
    for role in ("answer", "group"):
        if roles[role] and roles[role] not in present:
            print(f"  note: {role} field '{roles[role]}' absent; skipping")
            roles[role] = None
    roles["labels"] = [f for f in roles["labels"] if f in present]
    return roles


def prep(args):
    data_path = Path(args.data)
    if not data_path.exists():
        sys.exit(f"error: {data_path} not found. Run `audit.py fetch` first.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records, malformed = load(data_path)
    if not records:
        sys.exit(f"error: no records in {data_path}")
    roles = resolve_roles(args, records)
    name = args.name or data_path.stem
    print(f"{name}: {len(records)} records | text='{roles['text']}' "
          f"answer='{roles['answer']}' group='{roles['group']}'")

    schema = infer_schema(records)
    schema_issues = validate(records, schema)
    degenerate = find_degenerate(records, roles)

    dup_text = find_exact_dupes(
        records, lambda r: normalize_text(as_text(r.get(roles["text"]))))
    dup_answer = find_exact_dupes(
        records,
        lambda r: normalize_sql(as_text(r.get(roles["answer"])))
    ) if roles["answer"] else {}

    sigs = [shingles(as_text(r.get(roles["text"]))) for r in records]
    n = len(records)
    if n > args.lsh_threshold:
        method = "minhash-lsh"
        print(f"  n>{args.lsh_threshold}: MinHash-LSH "
              f"({MINHASH_PERMS} perms, {LSH_BANDS}x{LSH_ROWS}), "
              f"candidates verified exactly...")
        pairs, n_candidates = lsh_pairs(sigs, args.threshold,
                                        make_perms(MINHASH_PERMS))
    else:
        method = "exact-all-pairs"
        print(f"  n<={args.lsh_threshold}: exact all-pairs "
              f"({n * (n - 1) // 2} comparisons)...")
        pairs = all_pairs(sigs, args.threshold)
        n_candidates = n * (n - 1) // 2

    clusters = connected_components(pairs, n)
    identical, variant = classify_clusters(clusters, records, roles)
    dist = distributions(records, roles)

    def pct(x):
        return round(100.0 * x / n, 2) if n else 0.0

    summary = {
        "name": name,
        "source": str(data_path),
        "records": n,
        "roles": roles,
        "schema": schema,
        "malformed_lines": len(malformed),
        "schema_issues": len(schema_issues),
        "schema_issue_examples": schema_issues[:20],
        "degenerate_records": len(degenerate),
        "degenerate_examples": degenerate[:20],
        "distributions": dist,
        "similarity_method": method,
        "similarity_threshold": args.threshold,
        "candidate_pairs_examined": n_candidates,
        "duplicates": {
            "exact_duplicate_text_groups": len(dup_text),
            "exact_duplicate_text_records": sum(len(v) for v in dup_text.values()),
            "exact_duplicate_answer_groups": len(dup_answer),
            "exact_duplicate_answer_records": sum(len(v) for v in dup_answer.values()),
            "near_duplicate_pairs": len(pairs),
            "near_duplicate_clusters": len(clusters),
            "records_in_clusters": sum(len(c) for c in clusters),
            "fully_identical_clusters": len(identical),
            "records_fully_identical": sum(c["size"] for c in identical),
            "shared_stem_variant_clusters": len(variant),
        },
        "rates_pct": {
            "exact_duplicate_text": pct(sum(len(v) for v in dup_text.values())),
            "records_in_clusters": pct(sum(len(c) for c in clusters)),
            "fully_identical": pct(sum(c["size"] for c in identical)),
        },
        "fully_identical_detail": identical[:50],
        "shared_stem_variant_detail": variant[:50],
        "top_near_duplicate_pairs": [
            {"a": record_id(records[i], i), "b": record_id(records[j], j),
             "similarity": round(s, 4)}
            for i, j, s in sorted(pairs, key=lambda p: -p[2])[:25]],
    }

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "REPORT.md").write_text(render_report(summary), encoding="utf-8")

    if not args.no_prepared:
        with open(out_dir / "prepared.jsonl", "w", encoding="utf-8") as fh:
            for r in records:
                out = dict(r)
                out["_text_norm"] = normalize_text(as_text(r.get(roles["text"])))
                if roles["answer"]:
                    out["_answer_norm"] = normalize_sql(
                        as_text(r.get(roles["answer"])))
                fh.write(json.dumps(out, ensure_ascii=False) + "\n")

    d = summary["duplicates"]
    print(f"  schema issues {summary['schema_issues']} | "
          f"degenerate {summary['degenerate_records']} | "
          f"exact-dup text groups {d['exact_duplicate_text_groups']} | "
          f"clusters {d['near_duplicate_clusters']} "
          f"(identical {d['fully_identical_clusters']}, "
          f"variant {d['shared_stem_variant_clusters']}) | "
          f"identical rate {summary['rates_pct']['fully_identical']}%")
    print(f"  wrote {out_dir}/REPORT.md, {out_dir}/summary.json")


def render_report(s):
    d, dist, n = s["duplicates"], s["distributions"], s["records"]
    r = s["rates_pct"]
    lines = [
        f"# {s['name']} - integrity report", "",
        f"Generated by `audit.py prep` from `{s['source']}`.", "",
        "## Shape", "",
        f"- **{n}** records",
        f"- fields audited: text=`{s['roles']['text']}`, "
        f"answer=`{s['roles']['answer']}`, group=`{s['roles']['group']}`",
    ]
    if dist.get("distinct_groups"):
        lines += [
            f"- **{dist['distinct_groups']}** distinct "
            f"`{dist['group_field']}` values "
            f"({dist['items_per_group_mean']} items each on average)",
            f"- largest accounts for **{100 * dist['top_group_share']:.1f}%** "
            f"of all records",
        ]
    for label in s["roles"]["labels"]:
        if label in dist:
            lines += ["", f"| {label} | Records |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in dist[label].items()]

    lines += [
        "", "## Findings", "",
        f"- malformed JSON lines: **{s['malformed_lines']}**",
        f"- schema issues: **{s['schema_issues']}**",
        f"- degenerate records: **{s['degenerate_records']}**",
        f"- exact duplicate text groups: "
        f"**{d['exact_duplicate_text_groups']}** "
        f"({d['exact_duplicate_text_records']} records, "
        f"{r['exact_duplicate_text']}%)",
        f"- exact duplicate answer groups: "
        f"**{d['exact_duplicate_answer_groups']}** "
        f"({d['exact_duplicate_answer_records']} records)",
        f"- near-duplicate clusters at Jaccard >= "
        f"{s['similarity_threshold']}: **{d['near_duplicate_clusters']}** "
        f"({d['records_in_clusters']} records, {r['records_in_clusters']}%)",
        f"  - **{d['fully_identical_clusters']}** identical across every "
        f"evaluated field - {d['records_fully_identical']} records, "
        f"**{r['fully_identical']}%** - genuinely redundant",
        f"  - **{d['shared_stem_variant_clusters']}** share a text stem but "
        f"differ elsewhere - deliberate construction, not a defect",
        "", "## Method", "",
        f"Similarity: {SHINGLE_SIZE}-word shingles, Jaccard, threshold "
        f"{s['similarity_threshold']}. Search: **{s['similarity_method']}** "
        f"over {s['candidate_pairs_examined']} candidate pairs.",
        "",
    ]
    if s["similarity_method"] == "minhash-lsh":
        lines += [
            "LSH generates candidates only; every reported pair is verified "
            "with the true Jaccard score, so similarity values mean the same "
            "thing as under exact search. Recall is not guaranteed - a small "
            "number of genuine near-duplicates may be missed, which makes "
            "these counts a lower bound.", "",
        ]
    lines += ["Rerun `audit.py` to reproduce every number above.", ""]
    return "\n".join(lines)


# --- compare ---------------------------------------------------------------

def compare(args):
    paths = []
    for pattern in args.summaries:
        paths.extend(sorted(glob.glob(pattern)))
    if not paths:
        sys.exit("error: no summary.json files matched")

    rows = []
    for p in paths:
        s = json.loads(Path(p).read_text(encoding="utf-8"))
        d, r = s["duplicates"], s["rates_pct"]
        dist = s["distributions"]
        rows.append({
            "name": s["name"], "records": s["records"],
            "schema": s["schema_issues"], "degenerate": s["degenerate_records"],
            "identical": d["records_fully_identical"],
            "identical_pct": r["fully_identical"],
            "variant_clusters": d["shared_stem_variant_clusters"],
            "groups": dist.get("distinct_groups", "-"),
            "top_share": (f"{100 * dist['top_group_share']:.1f}%"
                          if dist.get("top_group_share") is not None else "-"),
            "method": s["similarity_method"],
        })
    rows.sort(key=lambda x: -x["identical_pct"])

    out = [
        "# Benchmark integrity comparison", "",
        "Every column produced by the same `audit.py prep` pass. "
        "\"Identical\" counts records duplicated across every evaluated "
        "field; shared-stem variants are listed separately because they are "
        "usually deliberate.", "",
        "| Dataset | Records | Schema issues | Degenerate | Identical dupes |"
        " Identical % | Variant clusters | Groups | Largest group |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for x in rows:
        out.append(
            f"| {x['name']} | {x['records']} | {x['schema']} | "
            f"{x['degenerate']} | {x['identical']} | {x['identical_pct']}% | "
            f"{x['variant_clusters']} | {x['groups']} | {x['top_share']} |")
    out += ["", "## Search method per dataset", "",
            "| Dataset | Method |", "|---|---|"]
    out += [f"| {x['name']} | {x['method']} |" for x in rows]
    out += ["", "MinHash-LSH rows are lower bounds: candidates are verified "
            "exactly, but recall is not guaranteed.", ""]

    text = "\n".join(out)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwrote {args.out}")


# --- leak ------------------------------------------------------------------

def cross_near_pairs(sigs_a, sigs_b, threshold, use_lsh):
    """Near-duplicate pairs spanning two sets. Returns (pairs, candidates).

    Under LSH both sets are bucketed together and only cross-set pairs are
    kept, so one banding pass serves both directions.
    """
    if not use_lsh:
        pairs = []
        for i, sa in enumerate(sigs_a):
            if not sa:
                continue
            for j, sb in enumerate(sigs_b):
                score = jaccard(sa, sb)
                if score >= threshold:
                    pairs.append((i, j, score))
        return pairs, len(sigs_a) * len(sigs_b)

    perms = make_perms(MINHASH_PERMS)
    n_a = len(sigs_a)
    combined = list(sigs_a) + list(sigs_b)
    signatures = {}
    for idx, s in enumerate(combined):
        sig = minhash_signature(s, perms)
        if sig is not None:
            signatures[idx] = sig

    candidates = set()
    for band in range(LSH_BANDS):
        lo, hi = band * LSH_ROWS, (band + 1) * LSH_ROWS
        buckets = defaultdict(list)
        for idx, sig in signatures.items():
            buckets[tuple(sig[lo:hi])].append(idx)
        for members in buckets.values():
            if len(members) < 2:
                continue
            left = [m for m in members if m < n_a][:200]
            right = [m for m in members if m >= n_a][:200]
            for x in left:
                for y in right:
                    candidates.add((x, y - n_a))

    pairs = []
    for i, j in candidates:
        score = jaccard(sigs_a[i], sigs_b[j])
        if score >= threshold:
            pairs.append((i, j, score))
    return pairs, len(candidates)


def leak(args):
    rec_a, _ = load(Path(args.a))
    rec_b, _ = load(Path(args.b))
    if not rec_a or not rec_b:
        sys.exit("error: one of the inputs is empty")
    roles = resolve_roles(args, rec_a + rec_b)
    name_a = args.name_a or Path(args.a).stem
    name_b = args.name_b or Path(args.b).stem
    print(f"{name_a} ({len(rec_a)}) vs {name_b} ({len(rec_b)}) | "
          f"text='{roles['text']}'")

    text_a = defaultdict(list)
    for i, r in enumerate(rec_a):
        k = normalize_text(as_text(r.get(roles["text"])))
        if k:
            text_a[k].append(i)
    exact_text = []
    for j, r in enumerate(rec_b):
        k = normalize_text(as_text(r.get(roles["text"])))
        if k in text_a:
            exact_text.append({"b": record_id(r, j),
                               "a": [record_id(rec_a[i], i)
                                     for i in text_a[k][:5]]})

    exact_answer = []
    if roles["answer"]:
        ans_a = defaultdict(list)
        for i, r in enumerate(rec_a):
            k = normalize_sql(as_text(r.get(roles["answer"])))
            if k:
                ans_a[k].append(i)
        for j, r in enumerate(rec_b):
            k = normalize_sql(as_text(r.get(roles["answer"])))
            if k in ans_a:
                exact_answer.append({"b": record_id(r, j),
                                     "a": [record_id(rec_a[i], i)
                                           for i in ans_a[k][:5]]})

    # The sharpest measure: a B record whose text AND reference answer both
    # match the same A record is answerable from memory alone.
    both_matches = []
    if roles["answer"]:
        pair_a = defaultdict(list)
        for i, r in enumerate(rec_a):
            kt = normalize_text(as_text(r.get(roles["text"])))
            ka = normalize_sql(as_text(r.get(roles["answer"])))
            if kt and ka:
                pair_a[(kt, ka)].append(i)
        for j, r in enumerate(rec_b):
            kt = normalize_text(as_text(r.get(roles["text"])))
            ka = normalize_sql(as_text(r.get(roles["answer"])))
            if (kt, ka) in pair_a:
                both_matches.append({
                    "b": record_id(r, j), "text": as_text(r.get(roles["text"]))[:120],
                    "a": [record_id(rec_a[i], i) for i in pair_a[(kt, ka)][:5]]})

    groups_a = groups_b = shared_groups = None
    if roles["group"]:
        ga = {as_text(r.get(roles["group"])) for r in rec_a}
        gb = {as_text(r.get(roles["group"])) for r in rec_b}
        groups_a, groups_b = len(ga), len(gb)
        shared_groups = sorted(ga & gb)

    sigs_a = [shingles(as_text(r.get(roles["text"]))) for r in rec_a]
    sigs_b = [shingles(as_text(r.get(roles["text"]))) for r in rec_b]
    use_lsh = len(rec_a) * len(rec_b) > args.lsh_threshold ** 2
    print(f"  {'MinHash-LSH' if use_lsh else 'exact cross product'} "
          f"at threshold {args.threshold}...")
    pairs, n_candidates = cross_near_pairs(sigs_a, sigs_b, args.threshold,
                                           use_lsh)

    b_matched = sorted({j for _, j, _ in pairs})
    n_b = len(rec_b)

    def pct(x):
        return round(100.0 * x / n_b, 2) if n_b else 0.0

    summary = {
        "a": {"name": name_a, "source": args.a, "records": len(rec_a),
              "distinct_groups": groups_a},
        "b": {"name": name_b, "source": args.b, "records": n_b,
              "distinct_groups": groups_b},
        "roles": roles,
        "similarity_method": "minhash-lsh" if use_lsh else "exact-cross-product",
        "similarity_threshold": args.threshold,
        "candidate_pairs_examined": n_candidates,
        "shared_groups": shared_groups,
        "shared_group_count": len(shared_groups) if shared_groups is not None else None,
        "exact_text_matches": len(exact_text),
        "exact_text_pct_of_b": pct(len(exact_text)),
        "exact_answer_matches": len(exact_answer),
        "exact_answer_pct_of_b": pct(len(exact_answer)),
        "text_and_answer_matches": len(both_matches),
        "text_and_answer_pct_of_b": pct(len(both_matches)),
        "text_and_answer_examples": both_matches[:20],
        "near_duplicate_pairs": len(pairs),
        "b_records_with_near_match": len(b_matched),
        "b_records_with_near_match_pct": pct(len(b_matched)),
        "exact_text_examples": exact_text[:20],
        "exact_answer_examples": exact_answer[:20],
        "top_near_pairs": [
            {"a": record_id(rec_a[i], i), "b": record_id(rec_b[j], j),
             "similarity": round(s, 4)}
            for i, j, s in sorted(pairs, key=lambda p: -p[2])[:25]],
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "leak_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "LEAK_REPORT.md").write_text(render_leak(summary),
                                            encoding="utf-8")

    print(f"  exact text matches   {summary['exact_text_matches']} "
          f"({summary['exact_text_pct_of_b']}% of {name_b})")
    print(f"  exact answer matches {summary['exact_answer_matches']} "
          f"({summary['exact_answer_pct_of_b']}%)")
    print(f"  BOTH text+answer     {summary['text_and_answer_matches']} "
          f"({summary['text_and_answer_pct_of_b']}%)")
    print(f"  near matches         {summary['b_records_with_near_match']} "
          f"({summary['b_records_with_near_match_pct']}%)")
    if shared_groups is not None:
        print(f"  shared {roles['group']} values: {len(shared_groups)}")
    print(f"  wrote {out_dir}/LEAK_REPORT.md")


def render_leak(s):
    a, b = s["a"], s["b"]
    lines = [
        f"# Cross-split contamination: {a['name']} -> {b['name']}", "",
        f"Does anything in **{b['name']}** already appear in "
        f"**{a['name']}**? Overlap between a training split and the split "
        f"used to score a model inflates that score directly, which makes it "
        f"a more consequential defect than duplication inside either split.",
        "", "## Inputs", "",
        f"- {a['name']}: **{a['records']}** records",
        f"- {b['name']}: **{b['records']}** records",
    ]
    if s["shared_group_count"] is not None:
        lines.append(
            f"- `{s['roles']['group']}` values: {a['distinct_groups']} vs "
            f"{b['distinct_groups']}, **{s['shared_group_count']} shared**")
    lines += [
        "", "## Findings", "",
        f"| Check | Matches | % of {b['name']} |", "|---|---|---|",
        f"| Identical text | {s['exact_text_matches']} | "
        f"{s['exact_text_pct_of_b']}% |",
        f"| Identical reference answer | {s['exact_answer_matches']} | "
        f"{s['exact_answer_pct_of_b']}% |",
        f"| **Both identical (answerable from memory)** | "
        f"**{s['text_and_answer_matches']}** | "
        f"**{s['text_and_answer_pct_of_b']}%** |",
        f"| Near-duplicate text (Jaccard >= {s['similarity_threshold']}) | "
        f"{s['b_records_with_near_match']} | "
        f"{s['b_records_with_near_match_pct']}% |",
        "",
    ]
    if s["shared_group_count"] == 0:
        lines += [
            "The two splits share **no** "
            f"`{s['roles']['group']}` values, so they are drawn from disjoint "
            "sources by construction. Any text similarity found here is "
            "phrasing reuse across different underlying data rather than the "
            "same problem appearing twice - a much weaker form of overlap, "
            "and often intentional.", "",
        ]
    elif s["shared_group_count"]:
        lines += [
            f"The splits share **{s['shared_group_count']}** "
            f"`{s['roles']['group']}` values, so items can describe the same "
            "underlying source. Text-level matches here deserve closer "
            "reading than they would across disjoint sources.", "",
        ]
    lines += [
        "## Method", "",
        f"Search: **{s['similarity_method']}** over "
        f"{s['candidate_pairs_examined']} candidate pairs. Exact checks "
        "compare normalized text and comment-stripped reference answers.",
        "",
    ]
    if s["similarity_method"] == "minhash-lsh":
        lines += [
            "Both splits are bucketed together and only cross-split pairs "
            "kept; every candidate is verified with its true Jaccard score. "
            "Recall is not guaranteed, so these counts are lower bounds.", "",
        ]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="download a HF dataset split to jsonl")
    f.add_argument("--dataset", required=True)
    f.add_argument("--split", required=True)
    f.add_argument("--config", default=None)
    f.add_argument("--out", required=True)
    f.set_defaults(func=fetch)

    pr = sub.add_parser("prep", help="audit a jsonl export")
    pr.add_argument("--data", required=True)
    pr.add_argument("--out-dir", default="prepared")
    pr.add_argument("--name", default=None, help="label used in reports")
    pr.add_argument("--preset", default="generic", choices=sorted(PRESETS))
    pr.add_argument("--text-field", default=None)
    pr.add_argument("--answer-field", default=None)
    pr.add_argument("--group-field", default=None)
    pr.add_argument("--label-fields", default=None, help="comma-separated")
    pr.add_argument("--threshold", type=float, default=NEAR_DUP_THRESHOLD)
    pr.add_argument("--lsh-threshold", type=int, default=LSH_SWITCH_AT,
                    help="record count above which MinHash-LSH is used")
    pr.add_argument("--no-prepared", action="store_true",
                    help="skip writing prepared.jsonl")
    pr.set_defaults(func=prep)

    lk = sub.add_parser("leak", help="measure contamination between two splits")
    lk.add_argument("--a", required=True, help="training / reference split")
    lk.add_argument("--b", required=True, help="evaluation split")
    lk.add_argument("--name-a", default=None)
    lk.add_argument("--name-b", default=None)
    lk.add_argument("--out-dir", default="prepared/leak")
    lk.add_argument("--preset", default="generic", choices=sorted(PRESETS))
    lk.add_argument("--text-field", default=None)
    lk.add_argument("--answer-field", default=None)
    lk.add_argument("--group-field", default=None)
    lk.add_argument("--label-fields", default=None)
    lk.add_argument("--threshold", type=float, default=NEAR_DUP_THRESHOLD)
    lk.add_argument("--lsh-threshold", type=int, default=LSH_SWITCH_AT)
    lk.set_defaults(func=leak)

    c = sub.add_parser("compare", help="build a table from several summaries")
    c.add_argument("summaries", nargs="+", help="paths or globs")
    c.add_argument("--out", default="COMPARISON.md")
    c.set_defaults(func=compare)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
