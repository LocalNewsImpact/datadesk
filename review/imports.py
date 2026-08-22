"""The import protocol (SCOPE.md §2.4), as proven on the March CSV:
upload → column mapping → diff report first → explicit apply.

The diff classifies every proposed change per field before anything is
written: `mojibake_fix` when the incoming value is exactly ftfy's repair
of the stored one, `edit` otherwise, `unchanged` and `missing` for the
rest. The classification is information for the human running the
apply — both kinds apply the same way, through the audited write path.
"""

import csv
import io

from explorer.models import Article, Source
from review.services import WRITABLE, repair_text

MAX_ROWS = 20_000

# What an import can write to, and how its rows find their row.
#
# The key is always a UUID — the article's or the source's. A hostname
# is never an identifier here: it changes, it exists with and without a
# www. prefix, and two records can wear the same one. Matching on it
# quietly writes the right correction to the wrong publisher, so the
# join is the UUID and a file without one is refused rather than guessed
# at. Export from Datadesk, edit, re-import: the export carries the id.
#
# The fields are the target's side of the audited write boundary and
# nothing else: a source's `status` and `paused_at` are the crawler's
# operational state, not editorial metadata, so a spreadsheet cannot
# reach them however tempting the column looks.
TARGETS = {
    "articles": {
        "label": "Articles",
        "model": Article,
        "key_field": "id",
        "key_hints": ("id", "article_id", "uuid", "article_uuid"),
        "key_label": "article UUID",
    },
    "sources": {
        "label": "Sources (publishers)",
        "model": Source,
        "key_field": "id",
        "key_hints": ("source_id", "source_uuid", "id", "uuid"),
        "key_label": "source UUID",
    },
}

# Columns an import may target: the article side of the write boundary.
IMPORTABLE_FIELDS = WRITABLE[Article]


def importable_fields(target):
    return WRITABLE[TARGETS[target]["model"]]


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


def _owner_finding(incoming, current, known):
    """(value_to_write, kind, reason) for an incoming owner."""
    from datasets.owners import canonical_owner, fold

    canonical, kind = canonical_owner(incoming, known)
    # A record that already names an owner gets the more useful message,
    # whether or not the incoming name is one we know.
    if current and fold(current) != fold(canonical):
        return (
            None,
            "suspect",
            f"record already names {current!r}; an ownership change is a "
            "fact to confirm, not a spelling to fix",
        )
    if kind == "unknown":
        return (
            None,
            "suspect",
            f"{canonical!r} is not a known owner — add it deliberately "
            "or correct the spelling",
        )
    return canonical, "edit", ""


def _suspect(field, value, state):
    """Why an incoming publisher value should not be written, or ''.

    The same gazetteer checks the source form applies (SCOPE.md §2.4):
    an import is not a way around them.
    """
    text = (value or "").strip()
    if not text or field not in ("city", "county"):
        return ""
    from datasets.geo import canonical_county, states_with_county
    from datasets.places import validate_city

    if field == "county":
        if state:
            if canonical_county(state, text)[1]:
                return ""
            elsewhere = states_with_county(text)
            if elsewhere:
                return f"{text} is a county in {', '.join(elsewhere)}, not {state}"
            return f"{text} is not a county in {state}"
        return "" if states_with_county(text) else f"{text} is not a county anywhere"

    if not state:
        return ""
    known, hints = validate_city(state, text)
    if known:
        return ""
    return f"{text} is not a {state} place" + (
        f"; did you mean {', '.join(hints)}?" if hints else ""
    )


def guess_target(columns):
    """Which table a file is about, from its header row."""
    lowered = {c.lower() for c in columns}
    for name, target in TARGETS.items():
        if lowered & set(target["key_hints"]):
            return name
    return "articles"


def guess_key_column(columns, target="articles"):
    for hint in TARGETS[target]["key_hints"]:
        for column in columns:
            if column.lower() == hint:
                return column
    return ""


def compute_diff(batch):
    """The diff report: per-row, per-field classification against the
    current corpus. Nothing is written."""
    name = getattr(batch, "target", "articles") or "articles"
    target = TARGETS[name]
    model, key_field = target["model"], target["key_field"]
    keys = [str(row.get(batch.key_column, "") or "").strip() for row in batch.rows]
    found = model.objects.in_bulk([k for k in keys if k], field_name=key_field)

    # City and county are checked against the gazetteer when a state is
    # given, so an import cannot write what the source form would refuse.
    validate_state = (getattr(batch, "validate_state", "") or "").strip()
    # The corpus's own owner spellings are the vocabulary an import is
    # matched against.
    owners = (
        set(
            Source.objects.exclude(owner__isnull=True)
            .exclude(owner="")
            .values_list("owner", flat=True)
        )
        if name == "sources"
        else set()
    )

    changes = {}  # pk -> {field: incoming} — what apply would write
    report = []
    counts = {
        "unchanged": 0,
        "edit": 0,
        "mojibake_fix": 0,
        "suspect": 0,
        "missing": 0,
    }

    # Two rows keyed to the same record silently make the last one win,
    # which is how a sheet with a duplicated line quietly overwrites a
    # correction with a different one. Name them instead.
    seen_keys = set()
    duplicates = set()
    for key in keys:
        if key and key in seen_keys:
            duplicates.add(key)
        seen_keys.add(key)

    for row in batch.rows:
        pk = str(row.get(batch.key_column, "") or "").strip()
        article = found.get(pk)
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
            if not str(incoming).strip() and current:
                # An empty cell means "no value supplied", never "delete
                # what is recorded". Sheets are full of blanks that were
                # never meant as instructions; clearing a field is an
                # explicit edit, made in the UI where it is visible.
                counts["unchanged"] += 1
                continue
            suspect = ""
            if name == "sources" and field == "owner" and str(incoming).strip():
                canonical, kind, reason = _owner_finding(incoming, current, owners)
                if kind == "suspect":
                    suspect = reason
                else:
                    incoming = canonical
            elif name == "sources":
                suspect = _suspect(field, incoming, validate_state)
            if incoming == current:
                kind = "unchanged"
            elif suspect:
                # A value the gazetteer does not recognise is not applied
                # by this batch: an import must not launder a typo into
                # the corpus because it arrived in a spreadsheet.
                kind = "suspect"
            elif incoming == repair_text(current):
                kind = "mojibake_fix"
            else:
                kind = "edit"
            counts[kind] = counts.get(kind, 0) + 1
            if kind != "unchanged":
                if kind != "suspect":
                    row_changes[field] = incoming
                fields.append(
                    {
                        "field": field,
                        "kind": kind,
                        "current": current,
                        "incoming": incoming,
                        "reason": suspect,
                    }
                )
        if pk in duplicates:
            # Nothing from a duplicated key is applied: which of the two
            # rows was meant is not ours to guess.
            counts["duplicate"] = counts.get("duplicate", 0) + 1
            report.append(
                {
                    "id": pk,
                    "kind": "duplicate",
                    "fields": [
                        {
                            "field": f["field"],
                            "kind": "suspect",
                            "current": f["current"],
                            "incoming": f["incoming"],
                            "reason": "the file has more than one row for "
                            "this record; keep one",
                        }
                        for f in fields
                    ],
                }
            )
            continue
        if row_changes:
            changes[str(article.pk)] = row_changes
        # A row whose only finding is suspect applies nothing, but it is
        # exactly what the reviewer needs to see, so it still reports.
        if fields:
            report.append({"id": pk, "kind": "changed", "fields": fields})

    return {"counts": counts, "report": report, "changes": changes}
