"""The import protocol (SCOPE.md §2.3), as proven on the March CSV:
upload → column mapping → diff report first → explicit apply.

The diff classifies every proposed change per field before anything is
written: `mojibake_fix` when the incoming value is exactly ftfy's repair
of the stored one, `edit` otherwise, `unchanged` and `missing` for the
rest. The classification is information for the human running the
apply — both kinds apply the same way, through the audited write path.
"""

import csv
import io

from explorer.models import Article
from review.services import WRITABLE, repair_text

MAX_ROWS = 20_000

# Columns an import may target: the article side of the write boundary.
IMPORTABLE_FIELDS = WRITABLE[Article]

# Header names that mean "this is the article UUID join key".
_KEY_HINTS = ("id", "article_id", "uuid", "article_uuid")


class ImportError_(Exception):
    """A CSV that cannot enter the protocol; the message is user-facing."""


def parse_csv(uploaded_file, filename):
    """Read an uploaded CSV (UTF-8, BOM tolerated) into headers + rows."""
    try:
        text = uploaded_file.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportError_(
            "Not UTF-8. Exports and backpatch CSVs are UTF-8 (BOM welcome)."
        ) from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportError_("No header row found.")
    rows = list(reader)
    if not rows:
        raise ImportError_("No data rows found.")
    if len(rows) > MAX_ROWS:
        raise ImportError_(
            f"{len(rows)} rows; the batch limit is {MAX_ROWS}. Split the file."
        )
    return list(reader.fieldnames), rows


def guess_key_column(columns):
    for hint in _KEY_HINTS:
        for column in columns:
            if column.lower() == hint:
                return column
    return ""


def compute_diff(batch):
    """The diff report: per-row, per-field classification against the
    current corpus. Nothing is written."""
    ids = [row.get(batch.key_column, "").strip() for row in batch.rows]
    articles = Article.objects.in_bulk([i for i in ids if i])

    changes = {}  # pk -> {field: incoming} — what apply would write
    report = []
    counts = {"unchanged": 0, "edit": 0, "mojibake_fix": 0, "missing": 0}

    for row in batch.rows:
        pk = row.get(batch.key_column, "").strip()
        article = articles.get(pk)
        if article is None:
            counts["missing"] += 1
            report.append({"id": pk or "(blank)", "kind": "missing", "fields": []})
            continue
        fields = []
        row_changes = {}
        for csv_column, field in batch.column_map.items():
            if csv_column not in row:
                continue
            incoming = row[csv_column]
            current = getattr(article, field) or ""
            if incoming == current:
                kind = "unchanged"
            elif incoming == repair_text(current):
                kind = "mojibake_fix"
            else:
                kind = "edit"
            counts[kind] += 1
            if kind != "unchanged":
                row_changes[field] = incoming
                fields.append(
                    {
                        "field": field,
                        "kind": kind,
                        "current": current,
                        "incoming": incoming,
                    }
                )
        if row_changes:
            changes[pk] = row_changes
            report.append({"id": pk, "kind": "changed", "fields": fields})

    return {"counts": counts, "report": report, "changes": changes}
