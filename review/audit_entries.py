"""What one audit entry did, and what reverting it would do now.

The log listed an action, a table, a row count and sixty characters of
reason, and offered a Revert button beside every one of them. None of
that answers the question the button asks: reverting writes recorded
values back over whatever the rows hold today, and whether that is
right depends on three things the list never showed --

- what the entry actually changed, field by field;
- whether the rows still hold what it wrote, or something later moved
  them, in which case a revert discards that later work;
- whether the entry can be reverted at all. Half the log is written on
  tables outside the write boundary (`auth_user`, `accounts_invitation`,
  `visuals`), where `revert()` raises rather than writes.

So this module reads the live rows and reports the comparison. It
decides nothing: the page shows it and a person chooses.
"""

import json

from django.db import DatabaseError
from django.urls import reverse

from explorer.dberrors import absent_or_raise
from review.audit_shapes import (
    CREATION,
    DELETION,
    NOTE,
    SESSION,
    by_id,
    is_per_row,
    shape_of,
)
from review.services import _BY_TABLE, DELETABLE, SUBJECT_MODELS, _read


def _after_for(entry, shape, row_id):
    """The values this entry wrote to one row.

    `audited_update` records one `{field: value}` map for every row it
    touched; `audited_update_rows` records a map per row; a session
    records a list. All three are read here so the page does not have to
    know which path wrote the entry.
    """
    if shape == SESSION:
        return by_id(entry.after).get(row_id, {})
    after = entry.after
    if is_per_row(after, entry.target_ids):
        return after.get(row_id, {})
    return after if isinstance(after, dict) else {}


def show(value):
    """A recorded value as a person reads it."""
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value == "":
        return "(empty)"
    return str(value)


def _link(table, row_id):
    """Where the row can be looked at, for the tables that have a page.

    Both names for the same thing: the audited write path records the
    database table (`articles`), and a queue session records the subject
    type it asks about (`article`).
    """
    if table in ("articles", "article"):
        return reverse("explorer:article_detail", args=[row_id])
    return ""


def _live_rows(model, row_ids):
    """The rows as they stand, keyed by id, or None if the crawler
    database is not reachable.

    A checkout with no crawler connection still has to render this page
    -- the entry and what it recorded are in this database -- so an
    absent crawler drops the "now" column rather than the page. A query
    this repository got wrong is re-raised (explorer/dberrors.py).
    """
    try:
        return {str(o.pk): o for o in model.objects.filter(pk__in=list(row_ids))}
    except DatabaseError as exc:
        absent_or_raise(exc, "review.audit_entries.live_rows")
        return None


#: What a queue session records about the decision rather than about the
#: row: which verb was chosen, and the qualifier it was given. Shown
#: beside the row, not as columns of it -- neither is a field of an
#: article.
DECISION_KEYS = ("verb", "value")


def _fields(entry, shape, row_id, recorded, row):
    """One row's fields: what it held, what was written, what it holds."""
    written = _after_for(entry, shape, row_id)
    names = [n for n in (list(recorded) or list(written)) if n not in DECISION_KEYS]
    fields = []
    for name in names:
        wrote = written.get(name)
        now = _read(row, name) if row is not None else None
        fields.append(
            {
                "name": name,
                "before": show(recorded.get(name)),
                "after": show(wrote),
                "now": show(now) if row is not None else "",
                # The value moved after this entry wrote it. Reverting
                # would discard whatever moved it, which is the one thing
                # a reviewer has to see before choosing.
                "drifted": row is not None and now != wrote,
            }
        )
    return fields


def _rows(entry, shape, model):
    """Each target row, with its fields and whether it is still there."""
    # A creation has no before-values; what it recorded is what it
    # wrote. Only the structured shapes have per-row values at all --
    # a note's `before` may be a list, a string, or anything else the
    # writer found useful, and none of it is addressed by row.
    recorded_by_id = {}
    if shape == SESSION:
        recorded_by_id = by_id(entry.before)
    elif shape != NOTE:
        recorded_by_id = (entry.after if shape == CREATION else entry.before) or {}

    ids = [str(i) for i in (entry.target_ids or [])]
    # A table outside the write boundary has no model to read, which is
    # not the same as the crawler being unreachable and must not be
    # reported as it.
    live = _live_rows(model, ids) if model is not None else {}
    rows = []
    for row_id in ids:
        row = live.get(row_id) if live else None
        wrote = _after_for(entry, shape, row_id)
        rows.append(
            {
                "id": row_id,
                "link": _link(entry.target_table, row_id),
                "present": (None if live is None or model is None else row is not None),
                "fields": _fields(
                    entry, shape, row_id, recorded_by_id.get(row_id, {}), row
                ),
                # The verb a reviewer chose, where one was recorded. It is
                # the decision the page is being read to judge, and it is
                # not a column of anything.
                "decision": " ".join(
                    str(wrote[key]) for key in DECISION_KEYS if wrote.get(key)
                ),
                "label": getattr(row, "title", "") if row is not None else "",
            }
        )
    return rows, live is not None


def _revertability(entry, shape, model):
    """Whether Revert can run, and what it would do -- said in words,
    because "the button is missing" is not an explanation."""
    if entry.reverted_by.exists():
        undone = entry.reverted_by.order_by("timestamp").last()
        return False, f"Already reverted by entry {undone.pk}.", undone
    if model is None:
        return (
            False,
            f"{entry.target_table} is outside the write boundary: this console "
            "cannot write it, so there is nothing to revert here. The record "
            "stands as history.",
            None,
        )
    if shape == NOTE:
        return (
            False,
            "This entry records what happened rather than a row-by-row change, "
            "so there are no values to write back.",
            None,
        )
    if shape == CREATION and model not in DELETABLE:
        return (
            False,
            f"Reverting a {model.__name__} creation would be a deletion, and the "
            "write boundary has no DELETE there. Correct the row instead.",
            None,
        )
    if shape == CREATION:
        return True, "Reverting deletes the rows this entry created.", None
    if shape == DELETION:
        return True, "Reverting recreates the rows this entry deleted.", None
    if shape == SESSION:
        return (
            True,
            "Reverting puts each status back and withdraws the decision, so the "
            "queue asks about these articles again.",
            None,
        )
    return True, "Reverting writes the recorded before-values back.", None


def detail(entry):
    """Everything the detail page shows about one entry."""
    shape = shape_of(entry)
    model = _BY_TABLE.get(entry.target_table) or SUBJECT_MODELS.get(entry.target_table)
    rows, crawler_connected = _rows(entry, shape, model)
    revertable, note, undone_by = _revertability(entry, shape, model)
    return {
        "entry": entry,
        "shape": shape,
        "rows": rows,
        "crawler_connected": crawler_connected,
        "revertable": revertable,
        "revert_note": note,
        "reverted_by": undone_by,
        # A revert is itself an entry, and reading one without saying what
        # it undid leaves the reader looking at values with no origin.
        "reverts": entry.reverts,
        # The note entries, whose before/after are not row values. Shown
        # as recorded and formatted here: a dict rendered by the template
        # arrives as a Python repr.
        "note_before": show(entry.before),
        "note_after": show(entry.after),
        "missing": [row["id"] for row in rows if row["present"] is False],
    }
