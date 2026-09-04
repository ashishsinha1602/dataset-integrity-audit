"""Enumerate the public Civitai model catalogue.

    python crawl.py --out models.jsonl

Civitai publishes per-model licence permissions as structured fields, which
the Hugging Face Hub does not express at all: there, a licence is either
declared as a string or absent. Here a model states separately whether credit
may be omitted, whether commercial use is allowed and in what form, whether
derivatives are allowed, and whether a derivative may be relicensed. It also
flags `poi`, meaning the model depicts a real identifiable person.

Only the fields needed for a census are kept. Descriptions and version blobs
are dropped: they dominate the payload and are not needed to count anything.

Cursor pagination, resumable. Cursor is written to state.json after every page
so an interrupted run continues rather than restarting.

`poi` (the flag for a model depicting a real identifiable person) is returned
as false on every record this endpoint serves, including under nsfw=true and on
targeted celebrity queries. Treat it as unavailable rather than as measured
zero; nothing here supports a claim about real-person models.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://civitai.com/api/v1/models"
UA = "dataset-integrity-audit/0.1 (+https://github.com/ashishsinha1602/dataset-integrity-audit)"

KEEP = ("id", "name", "type", "nsfw", "nsfwLevel", "poi", "minor", "sfwOnly",
        "allowNoCredit", "allowCommercialUse", "allowDerivatives",
        "allowDifferentLicense", "availability", "supportsGeneration")


def slim(m):
    row = {k: m.get(k) for k in KEEP}
    row["creator"] = (m.get("creator") or {}).get("username")
    row["tags"] = m.get("tags") or []
    row["baseModels"] = m.get("baseModels") or []
    stats = m.get("stats") or {}
    for k in ("downloadCount", "thumbsUpCount", "thumbsDownCount", "commentCount"):
        row[k] = stats.get(k)
    versions = m.get("modelVersions") or []
    row["nversions"] = len(versions)
    # Publication date of the earliest version stands in for model age;
    # the model object itself carries no creation timestamp.
    dates = [v.get("createdAt") or v.get("publishedAt") for v in versions]
    dates = [d for d in dates if d]
    row["firstPublished"] = min(dates) if dates else None
    return row


def fetch(url, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                wait = 5 * (attempt + 1)
                print(f"  HTTP {e.code}, backing off {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise
        except Exception:
            if attempt < tries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="models.jsonl")
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--limit", type=int, default=100, help="page size, max 100")
    ap.add_argument("--delay", type=float, default=0.6, help="seconds between pages")
    ap.add_argument("--max-pages", type=int, default=100000)
    args = ap.parse_args()

    # A second concurrent run appends the same records and interleaves writes,
    # which silently doubles the file and corrupts lines. Refuse to start one.
    lock = Path(args.out).with_suffix(".lock")
    if lock.exists():
        sys.exit(f"error: {lock} exists; another crawl is running. "
                 f"Delete it only if you are certain no other process is active.")
    lock.write_text("running")

    state = Path(args.state)
    cursor, pages, seen = None, 0, 0
    if state.exists():
        s = json.loads(state.read_text())
        cursor, pages, seen = s.get("cursor"), s.get("pages", 0), s.get("seen", 0)
        print(f"resuming after {pages} pages / {seen:,} models")

    out = open(args.out, "a", encoding="utf-8")
    try:
        while pages < args.max_pages:
            # Without nsfw=true the endpoint silently serves only the SFW
            # subset: a 50-item default page returns 0 models with nsfw set,
            # while the same page with nsfw=true returns 14. Omitting it does
            # not narrow the census, it hides part of the population.
            q = {"limit": args.limit, "nsfw": "true"}
            if cursor:
                q["cursor"] = cursor
            data = fetch(f"{API}?{urllib.parse.urlencode(q)}")
            if not data:
                break
            items = data.get("items") or []
            for m in items:
                out.write(json.dumps(slim(m), ensure_ascii=False) + "\n")
            seen += len(items)
            pages += 1
            cursor = (data.get("metadata") or {}).get("nextCursor")
            out.flush()
            state.write_text(json.dumps({"cursor": cursor, "pages": pages,
                                         "seen": seen}))
            if pages % 25 == 0:
                print(f"  {pages} pages, {seen:,} models", flush=True)
            if not cursor or not items:
                print("reached end of cursor")
                break
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\ninterrupted; state saved, rerun to resume")
    finally:
        out.close()
        lock.unlink(missing_ok=True)
    print(f"done: {pages} pages, {seen:,} models -> {args.out}")


if __name__ == "__main__":
    main()
