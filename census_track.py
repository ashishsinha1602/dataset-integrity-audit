"""Longitudinal census of the MCP registry.

    python census_track.py            # fetch, measure, append, report drift
    python census_track.py --history  # print every census recorded so far

Runs the same measurements as the one-off census and appends a row to
`census-history.csv`. A single census says what the registry contains; a
series says how it is changing, which nothing else currently tracks.

Deliberately re-fetches rather than reusing a cached file: the point is to
observe drift, and drift is invisible if the input never changes.
"""

import argparse
import collections
import csv
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENDPOINT = "https://registry.modelcontextprotocol.io/v0/servers"
META_KEY = "io.modelcontextprotocol.registry/official"
UA = "dataset-integrity-audit/0.1 (+https://github.com/ashishsinha1602/dataset-integrity-audit)"
CSV_PATH = Path(__file__).with_name("census-history.csv")
TEMPLATE = "describe what your server does"

FIELDS = ["date", "version_records", "distinct_servers", "publishers",
          "single_server_publishers", "top10_share_pct", "deprecated",
          "no_transport", "no_repository", "template_placeholder",
          "max_versions_one_server", "dup_desc_naive_pct",
          "dup_desc_correct_pct"]


def fetch_all(limit=100, max_pages=0):
    records, cursor, pages = [], None, 0
    while True:
        params = {"limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        req = urllib.request.Request(f"{ENDPOINT}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError) as exc:
            # A partial fetch would silently understate every count, so refuse
            # to record it rather than poison the series.
            sys.exit(f"error: fetch failed after {pages} pages ({exc}); "
                     "not recording a partial census")
        batch = data.get("servers") or []
        if not batch:
            break
        records.extend(batch)
        pages += 1
        cursor = (data.get("metadata") or {}).get("nextCursor")
        if pages % 100 == 0:
            print(f"  {pages} pages / {len(records)} records", flush=True)
        if not cursor or (max_pages and pages >= max_pages):
            break
    return records


def flatten(record):
    server = dict(record.get("server") or {})
    meta = (record.get("_meta") or {}).get(META_KEY) or {}
    server["_status"] = meta.get("status")
    server["_isLatest"] = meta.get("isLatest")
    return server


def measure(flat):
    latest = [r for r in flat if r.get("_isLatest")]
    n = len(latest)
    if not n:
        sys.exit("error: no records flagged isLatest; registry schema may have "
                 "changed - check before trusting this series")

    publishers = collections.Counter(r.get("name", "").split("/")[0]
                                     for r in latest)
    top10 = sum(v for _, v in publishers.most_common(10))
    versions = collections.Counter(r.get("name") for r in flat)

    def desc(rows):
        c = collections.Counter((r.get("description") or "").strip().lower()
                                for r in rows)
        return sum(v for v in c.values() if v > 1)

    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "version_records": len(flat),
        "distinct_servers": n,
        "publishers": len(publishers),
        "single_server_publishers": sum(1 for v in publishers.values() if v == 1),
        "top10_share_pct": round(100.0 * top10 / n, 2),
        "deprecated": sum(1 for r in latest if r.get("_status") == "deprecated"),
        "no_transport": sum(1 for r in latest
                            if not r.get("remotes") and not r.get("packages")),
        "no_repository": sum(1 for r in latest if not r.get("repository")),
        "template_placeholder": sum(
            1 for r in latest if TEMPLATE in (r.get("description") or "").lower()),
        "max_versions_one_server": max(versions.values()) if versions else 0,
        "dup_desc_naive_pct": round(100.0 * desc(flat) / max(len(flat), 1), 2),
        "dup_desc_correct_pct": round(100.0 * desc(latest) / n, 2),
    }


def load_history():
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def save(row):
    history = [h for h in load_history() if h.get("date") != row["date"]]
    history.append({k: str(row.get(k, "")) for k in FIELDS})
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(history)
    return history


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--max-pages", type=int, default=0)
    args = ap.parse_args()

    if args.history:
        hist = load_history()
        if not hist:
            print("no census recorded yet")
            return
        print(f"{'date':12} {'servers':>8} {'publishers':>11} "
              f"{'no-transport':>13} {'deprecated':>11}")
        for h in hist:
            print(f"{h['date']:12} {h['distinct_servers']:>8} "
                  f"{h['publishers']:>11} {h['no_transport']:>13} "
                  f"{h['deprecated']:>11}")
        return

    print("fetching the full registry (~830 pages, a few minutes)...")
    flat = [flatten(r) for r in fetch_all(max_pages=args.max_pages)]
    row = measure(flat)
    history = save(row)

    print(f"\nMCP registry census  {row['date']}")
    print(f"  {row['version_records']} version records -> "
          f"{row['distinct_servers']} distinct servers")
    print(f"  publishers {row['publishers']} "
          f"({row['single_server_publishers']} with exactly one) | "
          f"top-10 share {row['top10_share_pct']}%")
    print(f"  no transport {row['no_transport']} | "
          f"deprecated {row['deprecated']} | "
          f"no repository {row['no_repository']} | "
          f"template {row['template_placeholder']}")
    print(f"  duplicate descriptions: {row['dup_desc_naive_pct']}% naive vs "
          f"{row['dup_desc_correct_pct']}% deduplicated")

    if len(history) > 1:
        prev = history[-2]
        print(f"\n  drift since {prev['date']}:")
        for key, label in (("distinct_servers", "servers"),
                           ("publishers", "publishers"),
                           ("no_transport", "unreachable"),
                           ("deprecated", "deprecated")):
            try:
                d = int(row[key]) - int(prev[key])
                print(f"    {label:12} {d:+d}")
            except (ValueError, KeyError):
                pass
    else:
        print("\n  first census recorded; run again later to see drift")


if __name__ == "__main__":
    main()
