"""Daily traction tracker for the audit repo.

Records stars, unique visitors, clones and referrers to a CSV so Report #2
gets planned against real numbers instead of guesses.

    python track.py            # record today and print the delta
    python track.py --history  # print everything recorded so far

Uses the gh CLI, so it relies on the existing login rather than any token
handled here. GitHub's traffic API only retains 14 days, which is the whole
reason this runs daily instead of being queried on demand.
"""

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = "ashishsinha1602/bird-critic-audit"
GH = r"C:\Program Files\GitHub CLI\gh.exe"
CSV_PATH = Path(__file__).with_name("traction.csv")
FIELDS = ["date", "stars", "forks", "watchers", "views_14d", "uniques_14d",
          "clones_14d", "unique_cloners_14d", "top_referrers"]


def gh_api(path, jq=None):
    cmd = [GH, "api", path]
    if jq:
        cmd += ["--jq", jq]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        sys.exit(f"error: gh not found at {GH}. Install it or fix the path.")
    if out.returncode != 0:
        err = (out.stderr or "").strip().splitlines()
        raise RuntimeError(err[-1] if err else f"gh exited {out.returncode}")
    return out.stdout.strip()


def collect():
    repo = json.loads(gh_api(f"repos/{REPO}"))
    row = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "watchers": repo.get("subscribers_count", 0),
    }

    # Traffic endpoints need push access; degrade to blank rather than dying.
    for key, path, field in (
            ("views", f"repos/{REPO}/traffic/views", "count"),
            ("clones", f"repos/{REPO}/traffic/clones", "count")):
        try:
            data = json.loads(gh_api(path))
            row[f"{key}_14d"] = data.get(field, 0)
            row[("uniques_14d" if key == "views" else "unique_cloners_14d")] = \
                data.get("uniques", 0)
        except Exception as exc:
            print(f"  note: {key} traffic unavailable ({exc})")
            row[f"{key}_14d"] = ""
            row["uniques_14d" if key == "views" else "unique_cloners_14d"] = ""

    try:
        refs = json.loads(gh_api(f"repos/{REPO}/traffic/popular/referrers"))
        row["top_referrers"] = "; ".join(
            f"{r['referrer']}:{r['uniques']}" for r in refs[:5]) or "-"
    except Exception:
        row["top_referrers"] = ""
    return row


def load_history():
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def save(row):
    history = load_history()
    # One row per day; a re-run replaces today rather than duplicating it.
    history = [h for h in history if h.get("date") != row["date"]]
    history.append({k: str(row.get(k, "")) for k in FIELDS})
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(history)
    return history


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", action="store_true")
    args = ap.parse_args()

    if args.history:
        for h in load_history():
            print(f"{h['date']}  stars={h['stars']:>4}  "
                  f"uniques14d={h.get('uniques_14d',''):>4}  "
                  f"refs={h.get('top_referrers','')}")
        return

    row = collect()
    history = save(row)

    print(f"{REPO}  {row['date']}")
    print(f"  stars {row['stars']} | forks {row['forks']} | "
          f"watchers {row['watchers']}")
    print(f"  14d views {row['views_14d']} ({row['uniques_14d']} unique) | "
          f"clones {row['clones_14d']} ({row['unique_cloners_14d']} unique)")
    print(f"  referrers: {row['top_referrers']}")

    if len(history) > 1:
        prev = history[-2]
        try:
            delta = int(row["stars"]) - int(prev["stars"])
            arrow = "+" if delta >= 0 else ""
            print(f"\n  since {prev['date']}: {arrow}{delta} stars")
            if delta == 0:
                print("  no change - normal this early; the signal is the "
                      "referrer list, not the star count")
        except (ValueError, KeyError):
            pass
    else:
        print("\n  baseline recorded. Run again after posting to see movement.")


if __name__ == "__main__":
    main()
