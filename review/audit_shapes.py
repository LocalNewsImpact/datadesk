"""What shape an audit entry recorded, and how to read it by row.

Four writers put four shapes in one table, and both the page that reads
an entry and the path that reverts one have to know which is in front of
them. Asked in one place because they were disagreeing: the detail page
read a queue session's `before` -- a list -- as a map of rows and
raised, and `revert()` refuses the same entry for a different reason.

- **update** — `audited_update` and `audited_update_rows`: `before` and
  `after` are `{row id: {field: value}}`.
- **creation** / **deletion** — `audited_create` and `audited_delete`:
  the same map on one side, null on the other.
- **session** — `review/submit.py`, one entry for a session of queue
  decisions: `[{"id": …, "status": …}]` before and
  `[{"id": …, "verb": …, "value": …, "status": …}]` after. Row-by-row
  like the others, addressed by a key inside each record rather than by
  the key of a map.
- **note** — everything else in the log: an invitation, a role, a
  published visual, a chart's configuration. Recorded so somebody can
  read what happened, not so it can be written back.

The shape is decided by asking what is there, never by which side
happens to be null. Reading "before is null" as "a creation" made every
accounts note a creation, and then a `before` that was a list was read
as rows.
"""

UPDATE = "update"
CREATION = "creation"
DELETION = "deletion"
SESSION = "session"
NOTE = "note"

#: The key a session record addresses its row with.
ROW_ID = "id"


def _ids(target_ids):
    return {str(i) for i in (target_ids or [])}


def is_per_row(values, target_ids):
    """`{row id: {field: value}}`, keyed by exactly this entry's rows."""
    if not isinstance(values, dict) or not values:
        return False
    if set(values) != _ids(target_ids):
        return False
    return all(isinstance(v, dict) for v in values.values())


def is_session(values, target_ids):
    """`[{"id": row id, …}]`, the shape a queue session records."""
    if not isinstance(values, list) or not values:
        return False
    if not all(isinstance(v, dict) and ROW_ID in v for v in values):
        return False
    return {str(v[ROW_ID]) for v in values} <= _ids(target_ids)


def by_id(values):
    """A session's list as the same `{row id: {field: value}}` map the
    other shapes record, so one reader serves both."""
    if not isinstance(values, list):
        return {}
    return {
        str(record[ROW_ID]): {k: v for k, v in record.items() if k != ROW_ID}
        for record in values
        if isinstance(record, dict) and ROW_ID in record
    }


def shape_of(entry):
    """Which of the five an entry is."""
    ids = entry.target_ids
    if is_session(entry.before, ids) or is_session(entry.after, ids):
        return SESSION
    if entry.before is None and is_per_row(entry.after, ids):
        return CREATION
    if entry.after is None and is_per_row(entry.before, ids):
        return DELETION
    if is_per_row(entry.before, ids):
        return UPDATE
    return NOTE
