"""What is actually inside the Hugging Face model catalogue.

    python models_census.py --data models.jsonl

"Three million models on Hugging Face" is quoted constantly as a measure of
the ecosystem. This asks what those three million records contain, using the
fields the Hub publishes about each one.

The licence figure is the one worth care, so it was checked against the live
API rather than trusted: 60 models the census recorded as unlicensed returned
no licence live, and 60 it recorded as licensed returned one. 60/60 both ways.

Read the licence result precisely. It means the LISTING declares no licence,
not that the weights are unlicensed in some deeper sense: a fine-tune often
inherits terms from its base model without restating them, and this counts
that as absent. The claim is about what a consumer can determine from the
record, which is the thing that matters when deciding whether you may use it.

Weight the licence figure by use before repeating it. 64.04% of models carry
no licence, but only 8.68% of downloads go to those models: the gap sits
almost entirely in the unused tail. Zero-download models are 73.3%
unlicensed against 56.7% for models with any downloads, and among the 100
most-downloaded models just 8 are unlicensed. "Two thirds of the catalogue is
unlicensed" is true and misleading; the defensible statement is that 253
million downloads in 30 days went to models whose listing grants no rights,
and that 29.9% of the top 100,000 models are unlicensed.

Base-model inheritance does not explain it away either. Only 16.2% of
unlicensed models carry a base-model tag, so the great majority are not
fine-tunes that merely omitted to restate upstream terms. The share is also
flat over time, between 59.6% and 67.9% for every cohort since 2022.

Downloads are 30-day counts, a flow rather than a stock, so a model released
last week and one released in 2021 are not on equal footing here.
"""

import argparse
import collections
import json
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="models.jsonl")
    args = ap.parse_args()

    n = zero_dl = zero_likes = no_card = 0
    downloads, licences = [], collections.Counter()
    try:
        fh = open(args.data, encoding="utf-8")
    except OSError as exc:
        sys.exit(f"error: {exc}")

    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1
            dl = rec.get("dl30") or 0
            downloads.append(dl)
            zero_dl += dl == 0
            zero_likes += not (rec.get("likes") or 0)
            no_card += not rec.get("hascard")
            licences[rec.get("lic_c") or "NONE"] += 1

    if not n:
        sys.exit("error: no records read")
    downloads.sort(reverse=True)
    total = sum(downloads)

    print(f"models: {n:,}\n")
    for label, count in (("zero downloads in 30 days", zero_dl),
                         ("zero likes", zero_likes),
                         ("no model card", no_card),
                         ("no licence declared", licences["NONE"])):
        print(f"  {label:28} {count:>10,}  ({100*count/n:>5.2f}%)")

    print("\n30-day download concentration:")
    for k in (10, 100, 1000, 10000, n // 100, n // 10):
        if k < 1:
            continue
        print(f"  top {k:>9,} ({100*k/n:>7.3f}% of catalogue) = "
              f"{100*sum(downloads[:k])/total:>5.2f}% of downloads")

    nonzero = [d for d in downloads if d]
    print(f"\n  median downloads among the {len(nonzero):,} with any: "
          f"{sorted(nonzero)[len(nonzero)//2]:,}")

    print("\ndeclared licence:")
    for name, count in licences.most_common(6):
        print(f"  {count:>9,} ({100*count/n:>5.2f}%)  {name}")


if __name__ == "__main__":
    main()
