"""Retrieve the public Model Context Protocol server registry.

    python fetch_mcp_registry.py --out mcp-registry.jsonl

Pages by cursor until the endpoint stops returning one, flattening each record
into a single JSON object per line. Registry metadata (status, isLatest,
publishedAt) is hoisted onto the record with an underscore prefix so it can be
used as an ordinary field by the audit tooling.

Note the unit of analysis: the endpoint returns one record per published
*version*, not per server. Filter on `_isLatest` to get distinct servers.
Failing to do so inflates duplicate measurements by roughly 20x, because a
handful of publishers account for enormous version churn.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "https://registry.modelcontextprotocol.io/v0/servers"
META_KEY = "io.modelcontextprotocol.registry/official"
UA = "dataset-integrity-audit/0.1 (+https://github.com/ashishsinha1602/dataset-integrity-audit)"


def fetch_page(cursor, limit):
    params = {"limit": str(limit)}
    if cursor:
        params["cursor"] = cursor
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def flatten(record):
    server = dict(record.get("server") or {})
    meta = (record.get("_meta") or {}).get(META_KEY) or {}
    server["_status"] = meta.get("status")
    server["_isLatest"] = meta.get("isLatest")
    server["_publishedAt"] = meta.get("publishedAt")
    return server


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="mcp-registry.jsonl")
    ap.add_argument("--latest-out", default="mcp-latest.jsonl",
                    help="distinct servers only; pass empty string to skip")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    records, cursor, pages = [], None, 0
    while True:
        try:
            data = fetch_page(cursor, args.limit)
        except (urllib.error.URLError, TimeoutError) as exc:
            # Partial data is still usable; say how far we got rather than
            # exiting silently with a truncated file.
            print(f"stopped after {pages} pages: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            break
        batch = data.get("servers") or []
        if not batch:
            break
        records.extend(batch)
        pages += 1
        cursor = (data.get("metadata") or {}).get("nextCursor")
        if pages % 50 == 0:
            print(f"  {pages} pages / {len(records)} records")
        if not cursor:
            break

    print(f"retrieved {len(records)} version records in {pages} pages")

    flat = [flatten(r) for r in records]
    with open(args.out, "w", encoding="utf-8") as fh:
        for r in flat:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}")

    if args.latest_out:
        latest = [r for r in flat if r.get("_isLatest")]
        with open(args.latest_out, "w", encoding="utf-8") as fh:
            for r in latest:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"wrote {args.latest_out} ({len(latest)} distinct servers)")


if __name__ == "__main__":
    main()
