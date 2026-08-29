"""Extract github.com repo URLs from arXiv abstracts + comments.

Unlike the Papers-With-Code link table, this corpus is NEVER curated: a URL an
author typed into a 2016 abstract stays in arXiv metadata forever whether or not
the repo still exists. That makes it the right corpus for measuring true link rot.

Paper date = the v1 submission date.
"""
import pandas as pd, re, glob, json

# (?<![.\w]) prevents matching gist.github.com / raw.githubusercontent.com etc.,
# whose /user/HASH paths are NOT repositories and would spuriously 404.
GH_RE = re.compile(r"(?<![.\w])github\.com/([A-Za-z0-9][A-Za-z0-9_.-]{0,38})/([A-Za-z0-9_.-]{1,100})",
                   re.IGNORECASE)
GIST_HASH = re.compile(r"^[0-9a-f]{16,}$", re.I)
# owner path segments that are GitHub site pages, not users
BAD_OWNER = {"about", "features", "pricing", "explore", "topics", "collections",
             "trending", "marketplace", "sponsors", "readme", "orgs", "site",
             "settings", "notifications", "search", "login", "join", "blog",
             "apps", "security", "enterprise", "issues", "pulls", "new"}

ML_CATS = {"cs.LG", "cs.CV", "cs.CL", "cs.AI", "cs.NE", "stat.ML", "cs.IR",
           "cs.RO", "cs.MM", "cs.SD", "eess.AS", "eess.IV"}


def clean_repo(r):
    # strip trailing punctuation / latex artifacts that abut a URL in prose
    r = r.rstrip(".,;:)]}'\"")
    r = re.sub(r"\.git$", "", r, flags=re.I)
    if not r or r in (".", ".."):
        return None
    # a bare hex blob is a gist id, not a repo name
    if GIST_HASH.match(r):
        return None
    # trailing '-' or '_' means the URL was truncated at a line break in the
    # abstract; the slug is incomplete and would 404 spuriously
    if r.endswith("-") or r.endswith("_"):
        return None
    return r


def v1_date(versions):
    try:
        for v in versions:
            if v.get("version") == "v1":
                return pd.to_datetime(v.get("created"), errors="coerce", utc=True)
        if len(versions):
            return pd.to_datetime(versions[0].get("created"), errors="coerce", utc=True)
    except Exception:
        pass
    return pd.NaT


rows = []
for f in sorted(glob.glob("ax-*.parquet")):
    try:
        d = pd.read_parquet(f, columns=["id", "categories", "abstract", "comments", "versions"])
    except Exception as e:
        print("SKIP (unreadable/partial):", f, type(e).__name__, flush=True)
        continue
    txt = (d["abstract"].fillna("") + " \n " + d["comments"].fillna(""))
    hit = txt.str.contains("github.com", case=False, na=False)
    d = d[hit].copy()
    d["txt"] = txt[hit]
    for _, r in d.iterrows():
        dt = v1_date(r["versions"])
        if pd.isna(dt):
            continue
        cats = set((r["categories"] or "").split())
        for m in GH_RE.finditer(r["txt"]):
            owner, repo = m.group(1), clean_repo(m.group(2))
            if not repo or owner.lower() in BAD_OWNER:
                continue
            rows.append({"arxiv_id": r["id"], "slug": f"{owner}/{repo}",
                         "date": dt.tz_localize(None), "is_ml": bool(cats & ML_CATS)})
    print(f, "cum links", len(rows), flush=True)

L = pd.DataFrame(rows)
L.to_parquet("arxiv_links.parquet")
print("=" * 60)
print("mentions extracted:", len(L))
print("distinct papers:", L["arxiv_id"].nunique())
print("distinct repos:", L["slug"].str.lower().nunique())

for label, sub in (("ALL arXiv", L), ("ML categories", L[L["is_ml"]])):
    g = sub.groupby(sub["slug"].str.lower()).agg(
        slug=("slug", "first"), first_paper=("date", "min"),
        n_papers=("arxiv_id", "nunique"), is_ml=("is_ml", "max")).reset_index(drop=True)
    g["year"] = g["first_paper"].dt.year
    print(f"\n--- {label}: distinct repos {len(g)}")
    print(g.groupby("year").size().to_string())
    g.to_parquet("arxiv_repos_all.parquet" if label == "ALL arXiv" else "arxiv_repos_ml.parquet")
