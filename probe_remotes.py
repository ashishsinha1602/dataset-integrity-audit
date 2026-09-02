"""Probe every remote endpoint the MCP registry declares.

    python probe_remotes.py --data mcp-latest.jsonl --out remote-health.json

Independently checks the class of failure a registry census cannot see from
records alone: a server that declares a remote endpoint which no longer
exists. Issue #1579 covers servers declaring no transport; this covers servers
declaring one that is dead.

Failure is classified rather than pooled, because the distinction decides what
the number means:

  dns        - host does not resolve. Unambiguously gone.
  http_404   - host resolves, endpoint returns 404. The path is gone.
  tls        - certificate or handshake failure.
  auth       - 401/403. ALIVE but credentialed. NOT a failure; counting these
               as dead inflates the total roughly threefold.
  ok         - any other 2xx/3xx/4xx that is not 401/403/404.
  timeout    - no response in time. Unknown, not evidence of absence.

Only dns, http_404 and tls are counted as hard-unreachable.
"""

import argparse
import json
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = "dataset-integrity-audit/0.1 (+https://github.com/ashishsinha1602/dataset-integrity-audit)"
HARD = {"dns", "http_404", "tls"}


def classify(url, timeout):
    """Return (class, detail). Never raises."""
    try:
        host = urllib.parse.urlparse(url).hostname
    except ValueError:
        return "bad_url", ""
    if not host:
        return "bad_url", ""

    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return "dns", str(e.args[-1])[:60]
    except Exception as e:
        return "dns", type(e).__name__

    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return "ok", str(r.status)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "http_404", "404"
        if e.code in (401, 403):
            # Alive, just gated. Treating this as dead is the single easiest
            # way to overstate the result.
            return "auth", str(e.code)
        return "ok", str(e.code)
    except ssl.SSLError as e:
        return "tls", type(e).__name__
    except urllib.error.URLError as e:
        r = getattr(e, "reason", None)
        if isinstance(r, ssl.SSLError) or "CERTIFICATE" in str(r).upper():
            return "tls", str(r)[:60]
        if isinstance(r, socket.gaierror):
            return "dns", str(r)[:60]
        if isinstance(r, (TimeoutError, socket.timeout)):
            return "timeout", ""
        return "timeout", str(r)[:60]
    except (TimeoutError, socket.timeout):
        return "timeout", ""
    except Exception as e:
        return "timeout", type(e).__name__


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="mcp-latest.jsonl")
    ap.add_argument("--out", default="remote-health.json")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    path = Path(args.data)
    if not path.exists():
        sys.exit(f"error: {path} not found")

    targets = []
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if r.get("_status") != "active":
            continue
        for rem in (r.get("remotes") or []):
            url = rem.get("url")
            if url:
                targets.append((r["name"], rem.get("type"), url))

    servers = {t[0] for t in targets}
    print(f"{len(servers)} active servers declare {len(targets)} remotes")
    print(f"probing with {args.workers} workers, {args.timeout}s timeout...")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for (name, kind, url), (cls, detail) in zip(
                targets, pool.map(lambda t: classify(t[2], args.timeout), targets)):
            results.append({"server": name, "type": kind, "url": url,
                            "class": cls, "detail": detail})
            done += 1
            if done % 1000 == 0:
                hard = sum(1 for x in results if x["class"] in HARD)
                print(f"  {done}/{len(targets)} probed, {hard} hard-unreachable",
                      flush=True)

    by_class = Counter(x["class"] for x in results)
    hard_servers = {x["server"] for x in results if x["class"] in HARD}
    ok_servers = {x["server"] for x in results if x["class"] == "ok"}
    # A server with several remotes is only unreachable if none work.
    dead_servers = hard_servers - ok_servers

    summary = {
        "active_servers_with_remotes": len(servers),
        "remotes_probed": len(targets),
        "by_class": dict(by_class),
        "hard_unreachable_remotes": sum(by_class[c] for c in HARD),
        "servers_with_no_working_remote": len(dead_servers),
        "auth_walled_remotes": by_class.get("auth", 0),
        "timeouts_not_counted": by_class.get("timeout", 0),
        "dead_server_examples": sorted(dead_servers)[:25],
    }
    Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nby class: {dict(by_class)}")
    print(f"hard-unreachable remotes (dns/404/tls): "
          f"{summary['hard_unreachable_remotes']}")
    print(f"servers with NO working remote:         "
          f"{summary['servers_with_no_working_remote']}")
    print(f"auth-walled (alive, NOT counted dead):  "
          f"{summary['auth_walled_remotes']}")
    print(f"timeouts (unknown, NOT counted dead):   "
          f"{summary['timeouts_not_counted']}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
