"""Detect unmodified copies of Hugging Face Spaces from a census snapshot.

    python clone_detect.py --data spaces.csv

A Space is a git repository. Copying one by pushing its existing history
carries the original commit dates across, so the copy ends up with a
`last_modified` that predates its own `created_at`. That inversion is
impossible for an authored Space and is used here as the clone marker.

Note this is NOT the "Duplicate this Space" button: `cardData.duplicated_from`
is set on only 3.3% of the Spaces this finds, against 1.7% of a random control
-- no real difference. The button was the first hypothesis and it was wrong.
These are git-history copies, which the button does not account for.

Validation. Members of a family were re-queried live and compared on their HEAD
commit SHA, which is a hash of the full tree and history: 60/60 sampled members
shared their family's HEAD, while 12 random Spaces had 12 distinct HEADs. The
marker identifies byte-identical repositories, not merely similar ones.

The count is a LOWER BOUND. A copy of a source that was itself edited after the
copy was taken inherits a newer timestamp, shows no inversion, and is missed.
"""

import argparse
import collections
import csv
import sys
from datetime import datetime

# Only explicit error stages count as broken. SLEEPING wakes on request and
# PAUSED is an owner decision; folding either in would roughly double the rate
# on an assumption the data does not support.
BROKEN = {"RUNTIME_ERROR", "BUILD_ERROR", "CONFIG_ERROR", "NO_APP_FILE"}


def parse(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="spaces.csv")
    ap.add_argument("--include-static", action="store_true",
                    help="static Spaces have no runtime and cannot fail; "
                         "including them measures the CDN")
    args = ap.parse_args()

    csv.field_size_limit(10 ** 7)
    orig_n = orig_b = skipped = 0
    fam = collections.defaultdict(lambda: [0, 0])
    names = collections.Counter()

    try:
        fh = open(args.data, newline="", encoding="utf-8")
    except OSError as exc:
        sys.exit(f"error: {exc}")

    with fh:
        for row in csv.DictReader(fh):
            if not args.include_static and row["sdk"] == "static":
                continue
            created, modified = parse(row["created_at"]), parse(row["last_modified"])
            if not (created and modified):
                skipped += 1
                continue
            broken = row["stage"] in BROKEN
            if (modified - created).total_seconds() < 0:
                # Group on the inherited timestamp: one source history per key.
                entry = fam[row["last_modified"]]
                entry[0] += 1
                entry[1] += broken
                names[row["id"].split("/", 1)[-1].lower()] += 1
            else:
                orig_n += 1
                orig_b += broken

    clones = sum(n for n, _ in fam.values())
    clone_b = sum(b for _, b in fam.values())
    raw_n, raw_b = orig_n + clones, orig_b + clone_b
    if not raw_n:
        sys.exit("error: no usable rows")

    # A family collapses to one entry, broken if most of its copies are.
    fam_b = sum(1 for n, b in fam.values() if b * 2 > n)
    ded_n, ded_b = orig_n + len(fam), orig_b + fam_b

    print(f"rows with unparseable dates: {skipped:,}\n")
    print(f"{'':26}{'population':>13}{'broken':>11}{'rate':>9}")
    print(f"{'as listed':26}{raw_n:>13,}{raw_b:>11,}{100*raw_b/raw_n:>8.2f}%")
    print(f"{'clone-deduplicated':26}{ded_n:>13,}{ded_b:>11,}{100*ded_b/ded_n:>8.2f}%")

    print(f"\nclones {clones:,} ({100*clones/raw_n:.2f}% of catalogue) "
          f"in {len(fam):,} source histories")
    print(f"clone breakage {100*clone_b/clones:.2f}% vs "
          f"non-clone {100*orig_b/orig_n:.2f}%")
    print(f"catalogue shrinks {raw_n-ded_n:,} ({100*(raw_n-ded_n)/raw_n:.1f}%), "
          f"headline moves {100*ded_b/ded_n-100*raw_b/raw_n:+.2f} pp")

    print("\nmost-copied names:")
    for name, count in names.most_common(6):
        print(f"  {count:>6,}  {name[:50]}")


if __name__ == "__main__":
    main()
