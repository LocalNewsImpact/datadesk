"""Consolidate the historical CIN coding cohorts into one labelled set.

Cohorts A–E are five JotForm exports: 1,000 distinct articles, each
coded by two of groups A–D, with E a third coder brought in where the
two disagreed. 2,246 dispositions in all.

This applies the weighted voting scheme the thesis describes
(de Jesus, chapter 3), which is the scheme the production labels were
built on:

    primary counts 1.0, secondary counts 0.5
    concatenate every coder's primary and secondary, sum the weights,
    the highest total is the final label

    confidence follows from which combination won:
        primary + primary      0.9   "clean"
        primary + secondary    0.7   "fuzzy"
        secondary + secondary  0.4   discarded in the original study

WHY THE OUTPUT IS NOT COMMITTED
It carries the full article text of ~1,000 stories from Missouri
publishers. The labels are ours; the copy is not. `data/coding/` is
ignored, and this script regenerates the file from the source exports.

    python scripts/aggregate_coding_cohorts.py ~/Downloads --out data/coding/
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
from collections import defaultdict

CIN = (
    "Emergencies and Public Safety",
    "Health",
    "Education",
    "Transportation Systems",
    "Environment and Planning",
    "Economic Development",
    "Civic information",
    "Political life",
    "Sports",
    "Civic Life",
)

#: Reasons a coder could not classify. In the original form these sat in
#: the primary dropdown, which is why that column holds things that are
#: not categories.
REJECTIONS = (
    "NOT LOCAL",
    "NO APPROPRIATE CATEGORY",
    "TOO SHORT",
    "TECHNICAL ERROR",
    "NONE PRESENT (including technical issues)",
)

PRIMARY_WEIGHT = 1.0
SECONDARY_WEIGHT = 0.5

#: Confidence by the combination that won, from the thesis.
CLEAN, FUZZY, POOR = 0.9, 0.7, 0.4


def _clean(value: str | None) -> str:
    """Trimmed, and empty where the column is absent.

    Annotated `str | None` because that is what `dict.get` returns and
    what this handles: claiming `str` made every call site an error
    while the body was already correct.
    """
    return (value or "").strip()


def read_cohorts(folder: str) -> dict[str, list[dict]]:
    """{group letter: rows}, from the JotForm exports in this folder."""
    pattern = os.path.join(folder, "Missouri_News_Labeling_(Group_*.csv")
    out: dict[str, list[dict]] = {}
    for path in sorted(glob.glob(os.path.expanduser(pattern))):
        group = os.path.basename(path).split("Group_")[1][0]
        with open(path, encoding="utf-8-sig") as handle:
            out[group] = list(csv.DictReader(handle))
    if not out:
        raise SystemExit(f"no cohort exports found in {folder}")
    return out


def vote(dispositions: list[tuple[str, str]]) -> tuple[str, float, str]:
    """The winning label, its confidence, and how it was reached.

    `dispositions` is [(primary, secondary), ...], one per coder.
    """
    weights: dict[str, float] = defaultdict(float)
    # How each label earned its weight, so the confidence can say which
    # combination won rather than only how much it won by.
    as_primary: dict[str, int] = defaultdict(int)
    as_secondary: dict[str, int] = defaultdict(int)

    for primary, secondary in dispositions:
        if primary in CIN:
            weights[primary] += PRIMARY_WEIGHT
            as_primary[primary] += 1
        if secondary in CIN:
            weights[secondary] += SECONDARY_WEIGHT
            as_secondary[secondary] += 1

    if not weights:
        return "", 0.0, "no usable label"

    # Ties broken by primary count, then alphabetically, so the output is
    # deterministic and a rerun does not silently reorder the corpus.
    winner = max(
        weights,
        key=lambda label: (weights[label], as_primary[label], label),
    )
    primaries, secondaries = as_primary[winner], as_secondary[winner]

    if primaries >= 2:
        return winner, CLEAN, "primary+primary"
    if primaries == 1 and secondaries >= 1:
        return winner, FUZZY, "primary+secondary"
    if primaries == 1:
        return winner, FUZZY, "single primary"
    return winner, POOR, "secondary only"


def aggregate(cohorts: dict[str, list[dict]]) -> list[dict]:
    by_url: dict[str, dict] = {}
    for group, rows in cohorts.items():
        for row in rows:
            url = _clean(row.get("URL"))
            if not url:
                continue
            record = by_url.setdefault(
                url,
                {
                    "url": url,
                    "headline": _clean(row.get("Headline")),
                    "article": _clean(row.get("Article")),
                    "word_count": _clean(row.get("word_count")),
                    "legacy_article_id": _clean(row.get("article_id")),
                    "dispositions": [],
                    "groups": [],
                    "rejections": [],
                },
            )
            primary = _clean(row.get("Primary Category"))
            secondary = _clean(row.get("Secondary Category"))
            record["groups"].append(group)
            if primary in REJECTIONS:
                record["rejections"].append(primary)
            record["dispositions"].append((primary, secondary))

    out = []
    for record in by_url.values():
        label, confidence, how = vote(record["dispositions"])
        out.append(
            {
                "url": record["url"],
                "headline": record["headline"],
                "article": record["article"],
                "word_count": record["word_count"],
                "legacy_article_id": record["legacy_article_id"],
                "coders": len(record["dispositions"]),
                "groups": "".join(sorted(record["groups"])),
                "final_label": label,
                "confidence": confidence,
                "agreement": how,
                "rejections": "|".join(record["rejections"]),
            }
        )
    return sorted(out, key=lambda r: r["url"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="where the JotForm exports are")
    parser.add_argument("--out", default="data/coding")
    args = parser.parse_args()

    cohorts = read_cohorts(args.folder)
    rows = aggregate(cohorts)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "cohorts_aggregated.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[float, int] = defaultdict(int)
    for row in rows:
        counts[row["confidence"]] += 1
    print(f"cohorts: {', '.join(sorted(cohorts))}")
    print(f"articles: {len(rows)}  ->  {path}")
    for score in sorted(counts, reverse=True):
        print(f"   confidence {score}: {counts[score]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
