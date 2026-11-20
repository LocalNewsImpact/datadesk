"""Reading the 2023 V1 coding out of its JotForm exports.

Five files, Groups A to E: two coders per story (A with C or D, B with
C or D), and Group E a third coder brought in on some of the stories the
two disagreed about. 1,000 stories and 2,246 answers.

Kept apart from the models so the reading and the thesis rule can be
tested on rows without a database. See docs/CIN_V2_DATADESK.md, WP3.
"""

import csv
import re
from pathlib import Path
from urllib.parse import urlsplit

from review.classification import CIN_LABELS, ClassificationStory

#: The four things a V1 coder could say instead of a category, as the
#: JotForm menu spelled them, and what this queue calls each.
V1_REJECTIONS = {
    "NOT LOCAL": "not_local",
    "NO APPROPRIATE CATEGORY": "no_appropriate_category",
    "TOO SHORT": "too_short",
    "TECHNICAL ERROR": "technical_error",
    # An earlier wording of the form, used four times, all in Group A.
    # It says no category applies; that it also swept up technical
    # problems cannot be undone, and four answers do not justify a
    # reason of their own.
    "NONE PRESENT (INCLUDING TECHNICAL ISSUES)": "no_appropriate_category",
}

CIVIC = ("Civic information", "Civic Life")

_GROUP = re.compile(r"Group[_ ]([A-E])")


def group_of(path):
    """The cohort letter from a file name, or None."""
    found = _GROUP.search(Path(path).name)
    return found.group(1) if found else None


def read_rows(path):
    """One dict per answer in a JotForm export.

    The files do not agree on their headers: Group A's id column is
    `Article ID:` with a colon and the others' is without.
    """
    group = group_of(path)
    if group is None:
        raise ValueError(f"{Path(path).name}: no Group A-E in the file name")
    csv.field_size_limit(10**9)
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            story = (row.get("Article ID") or row.get("Article ID:") or "").strip()
            if not story:
                continue
            yield {
                "group": group,
                "story": story,
                "url": (row.get("URL") or "").strip(),
                "headline": (row.get("Headline") or "").strip(),
                "body": (row.get("Article") or "").strip(),
                "hash": (row.get("article_id") or "").strip(),
                "primary": (row.get("Primary Category") or "").strip(),
                "secondary": (row.get("Secondary Category") or "").strip(),
                "also": (row.get("Also Present") or "").strip(),
            }


def split_answer(primary):
    """(label, reject_reason) for a V1 primary. Raises on anything that
    is neither a category nor one of the four rejections: a value this
    cannot place is a value the baseline would silently lose."""
    if primary in CIN_LABELS:
        return primary, ""
    if primary.upper() in V1_REJECTIONS:
        return "", V1_REJECTIONS[primary.upper()]
    raise ValueError(f"unknown V1 primary category: {primary!r}")


def outcome(answers):
    """(outcome, label) for one story's V1 answers, by the thesis rule.

    Two coders with the same primary settle it: a label at 0.9, or a
    rejection if what they agreed on was that it should not be labelled.
    Failing that, a primary that is another coder's secondary is a label
    at 0.7. Anything else has no agreed label.
    """
    counts = {}
    for answer in answers:
        counts[answer["primary"]] = counts.get(answer["primary"], 0) + 1
    top = max(counts.items(), key=lambda item: item[1])
    if top[1] >= 2:
        label, reject = split_answer(top[0])
        if reject:
            return ClassificationStory.REJECTED, ""
        return ClassificationStory.LABEL_09, label
    secondaries = {a["secondary"] for a in answers if a["secondary"]}
    for answer in answers:
        if answer["primary"] in CIN_LABELS and answer["primary"] in secondaries:
            return ClassificationStory.LABEL_07, answer["primary"]
    return ClassificationStory.NO_LABEL, ""


def is_civic(answers):
    return any(a["primary"] in CIVIC or a["secondary"] in CIVIC for a in answers)


def collect(paths):
    """story id -> {"story": fields, "answers": [...]} over every file."""
    stories = {}
    for path in paths:
        for row in read_rows(path):
            entry = stories.setdefault(row["story"], {"story": row, "answers": []})
            # The first file to carry text for a story supplies it.
            if not entry["story"]["body"] and row["body"]:
                entry["story"] = row
            entry["answers"].append(row)
    return stories


def host_of(url):
    return (urlsplit(url).hostname or "")[:200]
