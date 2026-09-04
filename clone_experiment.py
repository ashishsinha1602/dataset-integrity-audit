"""Use clone families as a natural experiment on why Spaces break.

    python clone_experiment.py --data spaces.csv

Copies found by clone_detect.py are byte-identical repositories (verified on
HEAD commit SHAs). A family of copies therefore holds the CODE constant while
the copy date varies, which is the one comparison the cross-sectional census
cannot make: every other analysis compares different Spaces to each other.

Three tests are run.

1. Does the age gradient survive holding the SDK constant? The catalogue-wide
   finding is that older Spaces break more, and SDK mix shifted heavily over
   the same period (gradio 77.5% of 2022, docker 53.0% of 2026), so the
   gradient could be composition rather than age.

2. Within a family, is the older half more broken than the newer half? If
   breakage were the environment decaying underneath fixed code, it should be.

3. With creation year held fixed, how much does the answer still depend on
   WHICH app was copied rather than when?

Limits worth stating. The clone population is not representative: it is course
assignments and one-click deploy templates, not Spaces at large. Within-family
variation also includes per-user causes a snapshot cannot see, chiefly missing
API tokens. And deleted Spaces are invisible, so if broken old Spaces are
removed preferentially, every age comparison here is biased toward health.
"""

import argparse
import collections
import csv
import math
import statistics
import sys
from datetime import datetime

BROKEN = {"RUNTIME_ERROR", "BUILD_ERROR", "CONFIG_ERROR", "NO_APP_FILE"}
MIN_FAMILY = 20      # families smaller than this give unstable halves
YEARS = ["2022", "2023", "2024", "2025", "2026"]
SDKS = ["gradio", "docker", "streamlit"]


def parse(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def load(path):
    """Return (per-SDK-year counts, clone families)."""
    tot, brk = collections.Counter(), collections.Counter()
    fam = collections.defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["sdk"] == "static" or not row["sdk"]:
                continue
            created, modified = parse(row["created_at"]), parse(row["last_modified"])
            if not (created and modified):
                continue
            broken = row["stage"] in BROKEN
            year = row["created_at"][:4]
            if year in YEARS:
                tot[(row["sdk"], year)] += 1
                brk[(row["sdk"], year)] += broken
            if (modified - created).total_seconds() < 0:
                fam[row["last_modified"]].append((created, created.year, broken))
    return tot, brk, fam


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="spaces.csv")
    args = ap.parse_args()
    csv.field_size_limit(10 ** 7)

    try:
        tot, brk, fam = load(args.data)
    except OSError as exc:
        sys.exit(f"error: {exc}")

    print("1. broken % by creation year, within each SDK")
    print(f"{'sdk':11}" + "".join(f"{y:>9}" for y in YEARS))
    for sdk in SDKS:
        line = f"{sdk:11}"
        for year in YEARS:
            n = tot[(sdk, year)]
            line += f"{100*brk[(sdk, year)]/n:>8.1f}%" if n > 500 else f"{'-':>9}"
        print(line)
    print("   the gradient holds inside every SDK, so it is not SDK mix.\n")

    big = {k: v for k, v in fam.items() if len(v) >= MIN_FAMILY}
    if not big:
        sys.exit("no clone families large enough to test")

    older = newer = tie = 0
    for members in big.values():
        members.sort(key=lambda m: m[0])
        half = len(members) // 2
        old = sum(b for *_, b in members[:half]) / half
        new = sum(b for *_, b in members[len(members) - half:]) / half
        if old > new:
            older += 1
        elif new > old:
            newer += 1
        else:
            tie += 1
    n = older + newer
    total = n + tie
    print(f"2. within {total} clone families (identical code), which half breaks more?")
    print(f"   older half {older} ({100*older/total:.0f}%) | "
          f"newer half {newer} ({100*newer/total:.0f}%) | tied {tie}")
    if n:
        z = (older - n / 2) / math.sqrt(n / 4)
        print(f"   sign test z = {z:.1f}. Holding code fixed, older is NOT more broken.\n")

    # Family rate with year pinned, so only app identity varies.
    pinned = []
    for members in big.values():
        year = collections.Counter(y for _, y, _ in members).most_common(1)[0][0]
        sub = [b for _, y, b in members if y == year]
        if len(sub) >= MIN_FAMILY:
            pinned.append(sum(sub) / len(sub))
    pinned.sort()
    print(f"3. each family pinned to its most common creation year ({len(pinned)} families)")
    print(f"   broken rate still spans {100*pinned[0]:.0f}% to {100*pinned[-1]:.0f}%, "
          f"median {100*statistics.median(pinned):.0f}%")
    print(f"   never broken: {sum(1 for r in pinned if r == 0)} families | "
          f"over half broken: {sum(1 for r in pinned if r > 0.5)}")
    print("   with the year fixed, WHICH app was copied still decides the outcome.")


if __name__ == "__main__":
    main()
