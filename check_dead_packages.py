"""Live-check every npm/PyPI package the MCP registry points at.

    python check_dead_packages.py --data mcp-latest.jsonl

A registry entry can declare an installable package that no longer exists.
Scanning a package-index dump does not reliably detect this - a stale or
partial dump produces false positives - so every identifier is resolved
against the live registry and only an explicit 404 counts as missing.

Writes dead-packages.json with the full list so the claim is checkable.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = "dataset-integrity-audit/0.1 (+https://github.com/ashishsinha1602/dataset-integrity-audit)"
NPM = "https://registry.npmjs.org/"
PYPI = "https://pypi.org/pypi/{}/json"


def exists(url):
    """True/False if resolved, None if the check itself failed."""
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read(1)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return None          # 429, 5xx - unknown, not evidence of absence
    except Exception:
        return None


def check_npm(name):
    return name, exists(NPM + urllib.parse.quote(name, safe="@"))


def check_pypi(name):
    return name, exists(PYPI.format(urllib.parse.quote(name, safe="")))


def collect(rows, kind):
    out = set()
    for r in rows:
        for p in (r.get("packages") or []):
            rt = (p.get("registryType") or p.get("registry_name") or "").lower()
            ident = p.get("identifier") or p.get("name")
            if rt == kind and ident:
                out.add(ident)
    return sorted(out)


def run(names, fn, label, workers):
    missing, unknown = [], []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for name, ok in pool.map(fn, names):
            done += 1
            if ok is False:
                missing.append(name)
            elif ok is None:
                unknown.append(name)
            if done % 500 == 0:
                print(f"  {label}: {done}/{len(names)} checked, "
                      f"{len(missing)} missing", flush=True)
    return missing, unknown


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="mcp-latest.jsonl")
    ap.add_argument("--out", default="dead-packages.json")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    path = Path(args.data)
    if not path.exists():
        sys.exit(f"error: {path} not found")
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]

    npm_names = collect(rows, "npm")
    pypi_names = collect(rows, "pypi")
    print(f"{len(rows)} servers | {len(npm_names)} npm, "
          f"{len(pypi_names)} pypi identifiers to check")

    npm_missing, npm_unknown = run(npm_names, check_npm, "npm", args.workers)
    pypi_missing, pypi_unknown = run(pypi_names, check_pypi, "pypi", args.workers)

    result = {
        "servers": len(rows),
        "npm": {"checked": len(npm_names), "missing": len(npm_missing),
                "unresolved": len(npm_unknown),
                "missing_pct": round(100.0 * len(npm_missing) /
                                     max(len(npm_names), 1), 2),
                "missing_names": npm_missing},
        "pypi": {"checked": len(pypi_names), "missing": len(pypi_missing),
                 "unresolved": len(pypi_unknown),
                 "missing_pct": round(100.0 * len(pypi_missing) /
                                      max(len(pypi_names), 1), 2),
                 "missing_names": pypi_missing},
    }
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"\nnpm:  {len(npm_missing)}/{len(npm_names)} missing "
          f"({result['npm']['missing_pct']}%), "
          f"{len(npm_unknown)} unresolved")
    print(f"pypi: {len(pypi_missing)}/{len(pypi_names)} missing "
          f"({result['pypi']['missing_pct']}%), "
          f"{len(pypi_unknown)} unresolved")
    print(f"wrote {args.out}")
    print("\nunresolved entries are NOT counted as missing - a rate limit or "
          "5xx is not evidence a package is gone")


if __name__ == "__main__":
    main()
