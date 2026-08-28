"""The audited write path (SCOPE.md §2.2, boundary §6.5).

Every mutation of crawler data flows through here: the boundary is
checked (mirroring the datadesk_rw column grants — Postgres enforces it
anyway, this makes the refusal a clean error instead of a database
exception), the before/after values are captured, and an append-only
audit entry records actor, targets, and reason. Reverts apply the
`before` values back through the same path as a compensating action —
history is never edited.
"""

import ftfy
from django.db import router, transaction

from audit.models import AuditLogEntry
from explorer.models import Article, ArticleEnrichment, Dataset, DatasetSource, Source


class BoundaryViolation(Exception):
    """A write outside the SCOPE.md §6.5 column boundary."""


# The app-side mirror of infra/sql/create_crawler_write_role.sql.
WRITABLE = {
    Article: ("author", "title", "content", "text", "status", "wire_check_status"),
    ArticleEnrichment: (
        "skip_reason",
        "geo_skip_reason",
        # A reviewer can correct a scope the model got wrong. Use
        # set_scope() rather than writing these directly, so the
        # confidence is cleared with the value.
        "scope",
        "scope_confidence",
    ),
    # "meta.state" is a key inside a JSON column, not a column. The state
    # is a required field of a publisher record and had no way to be
    # written at all: not by the source form, not by accepting a proposal
    # that named it. Naming the key rather than opening `meta` keeps the
    # boundary a boundary -- everything else in that blob stays unwritable.
    Source: (
        "canonical_name",
        "city",
        "county",
        "owner",
        "type",
        "meta.state",
        # Named one by one for the same reason `meta.state` is: the key,
        # not the blob. A flag that proposes a fix to a field outside this
        # list is a question nobody can answer -- the queue offers the
        # change and applying it raises, which reached a reviewer as a
        # server error on submit.
        "meta.frequency",
    ),
    Dataset: (
        "name",
        "description",
        "meta",
        "cron_enabled",
        "owner_name",
        "owner_email",
    ),
}

# Phase 4 (SCOPE.md §2.5): what may be created, and the one thing that
# may be deleted — membership rows, because they are a mapping, not a
# record. Mirrors the INSERT/DELETE grants in
# infra/sql/create_crawler_write_role.sql.
CREATABLE = (Source, Dataset, DatasetSource)
DELETABLE = (DatasetSource,)

# Every model the audited path can touch, for resolving revert targets.
_BY_TABLE = {
    model._meta.db_table: model for model in {*WRITABLE, *CREATABLE, *DELETABLE}
}


def _read(obj, field):
    """A field, or a key inside a JSON column when the name is dotted."""
    column, _, key = field.partition(".")
    value = getattr(obj, column, None)
    return (value or {}).get(key) if key else value


def _write(obj, field, value):
    """The setter for the same. A dotted write replaces the key and leaves
    the rest of the blob alone -- writing the whole column would silently
    drop whatever else the record kept there."""
    column, _, key = field.partition(".")
    if not key:
        setattr(obj, column, value)
        return
    blob = dict(getattr(obj, column, None) or {})
    blob[key] = value
    setattr(obj, column, blob)


def audited_update(actor, instances, changes, action, reason=""):
    """Apply `changes` to each instance and record one audit entry.

    `instances` are rows of one crawler model; `changes` maps field →
    new value, all within the write boundary. Returns the audit entry.

    Ordering: the row updates are held open in a transaction on the
    write alias while the audit entry commits, then the rows commit. A
    failed row write therefore rolls everything back; the narrow failure
    window (audit committed, row commit fails) errs toward an audit
    entry for an unapplied change — recorded intent — never the reverse.
    """
    instances = list(instances)
    if not instances:
        raise ValueError("Nothing to update")
    model = type(instances[0])
    allowed = WRITABLE.get(model)
    if allowed is None:
        raise BoundaryViolation(f"{model.__name__} is not writable")
    outside = set(changes) - set(allowed)
    if outside:
        raise BoundaryViolation(
            f"{model.__name__} fields outside the write boundary: {sorted(outside)}"
        )

    before = {
        str(obj.pk): {field: _read(obj, field) for field in changes}
        for obj in instances
    }

    write_alias = router.db_for_write(model)
    columns = sorted({field.partition(".")[0] for field in changes})
    with transaction.atomic(using=write_alias):
        for obj in instances:
            for field, value in changes.items():
                _write(obj, field, value)
            obj.save(using=write_alias, update_fields=columns)
        entry = AuditLogEntry.objects.create(
            actor=actor,
            action=action,
            target_table=model._meta.db_table,
            target_ids=list(before),
            before=before,
            after=dict(changes),
            reason=reason,
        )
    return entry


def revert(actor, entry, reason=""):
    """Apply an audit entry's `before` values back, as a new audited action.

    Rows that no longer exist are skipped and named in the compensating
    entry's reason rather than failing the rest.
    """
    model = _BY_TABLE.get(entry.target_table)
    if model is None:
        raise BoundaryViolation(f"{entry.target_table} is not writable")
    if entry.before is None:
        return _revert_creation(actor, entry, model, reason)
    if entry.after is None:
        return _revert_deletion(actor, entry, model, reason)

    write_alias = router.db_for_write(model)
    missing = []
    reverted_ids = []
    before = {}

    with transaction.atomic(using=write_alias):
        for pk, values in (entry.before or {}).items():
            obj = model.objects.filter(pk=pk).first()
            if obj is None:
                missing.append(pk)
                continue
            before[pk] = {field: _read(obj, field) for field in values}
            for field, value in values.items():
                _write(obj, field, value)
            obj.save(
                using=write_alias,
                update_fields=sorted({f.partition(".")[0] for f in values}),
            )
            reverted_ids.append(pk)

        note = reason or f"revert of audit entry {entry.pk}"
        if missing:
            note += f" (rows no longer present: {', '.join(missing)})"
        applied = {pk: (entry.before or {})[pk] for pk in reverted_ids}
        compensating = AuditLogEntry.objects.create(
            actor=actor,
            action=f"revert:{entry.action}",
            target_table=entry.target_table,
            target_ids=reverted_ids,
            before=before,
            after=applied,
            reason=note,
        )
    return compensating


# Repair encoding, don't editorialize: ftfy's default also uncurls smart
# quotes and similar typography, which would rewrite text that was never
# broken. This config fixes mojibake and nothing else.
_FTFY_CONFIG = ftfy.TextFixerConfig(uncurl_quotes=False)


def repair_text(value):
    return ftfy.fix_text(value, config=_FTFY_CONFIG)


def audited_update_rows(actor, model, rows, action, reason=""):
    """Like audited_update, but each row carries its own values — the
    import apply path (SCOPE.md §2.4). `rows` maps pk → {field: value}.
    Fields must sit inside the write boundary; missing rows fail the
    whole batch (the diff ran moments earlier — a vanished row means
    the corpus moved under us, and half-applied batches are worse than
    a retry). Returns the audit entry, whose before/after are per-id
    maps, the shape revert() expects.
    """
    allowed = WRITABLE.get(model)
    if allowed is None:
        raise BoundaryViolation(f"{model.__name__} is not writable")
    fields = {field for values in rows.values() for field in values}
    outside = fields - set(allowed)
    if outside:
        raise BoundaryViolation(
            f"{model.__name__} fields outside the write boundary: {sorted(outside)}"
        )
    if not rows:
        raise ValueError("Nothing to update")

    write_alias = router.db_for_write(model)
    before = {}
    with transaction.atomic(using=write_alias):
        for pk, values in rows.items():
            obj = model.objects.filter(pk=pk).first()
            if obj is None:
                raise ValueError(f"Row {pk} no longer exists")
            before[pk] = {field: _read(obj, field) for field in values}
            for field, value in values.items():
                _write(obj, field, value)
            obj.save(
                using=write_alias,
                update_fields=sorted({f.partition(".")[0] for f in values}),
            )
        entry = AuditLogEntry.objects.create(
            actor=actor,
            action=action,
            target_table=model._meta.db_table,
            target_ids=list(rows),
            before=before,
            after=dict(rows),
            reason=reason,
        )
    return entry


def _row_values(obj):
    """Every concrete field, JSON-safe, for creation/deletion audit records."""
    values = {}
    for field in type(obj)._meta.concrete_fields:
        value = getattr(obj, field.attname)
        values[field.attname] = value
    return values


def audited_create(actor, instances, action, reason=""):
    """Create rows with a full-values audit record (before=None)."""
    instances = list(instances)
    if not instances:
        raise ValueError("Nothing to create")
    model = type(instances[0])
    if model not in CREATABLE:
        raise BoundaryViolation(f"{model.__name__} is not creatable")

    write_alias = router.db_for_write(model)
    with transaction.atomic(using=write_alias):
        for obj in instances:
            obj.save(using=write_alias, force_insert=True)
        after = {str(o.pk): _row_values(o) for o in instances}
        entry = AuditLogEntry.objects.create(
            actor=actor,
            action=action,
            target_table=model._meta.db_table,
            target_ids=list(after),
            before=None,
            after=after,
            reason=reason,
        )
    return entry


def audited_delete(actor, instances, action, reason=""):
    """Delete rows with a full-values audit record (after=None), so a
    revert can recreate them."""
    instances = list(instances)
    if not instances:
        raise ValueError("Nothing to delete")
    model = type(instances[0])
    if model not in DELETABLE:
        raise BoundaryViolation(f"{model.__name__} is not deletable")

    write_alias = router.db_for_write(model)
    before = {str(o.pk): _row_values(o) for o in instances}
    with transaction.atomic(using=write_alias):
        for obj in instances:
            obj.delete(using=write_alias)
        entry = AuditLogEntry.objects.create(
            actor=actor,
            action=action,
            target_table=model._meta.db_table,
            target_ids=list(before),
            before=before,
            after=None,
            reason=reason,
        )
    return entry


def _revert_creation(actor, entry, model, reason):
    """Reverting a creation is a deletion — possible only where the
    boundary allows deletes at all."""
    if model not in DELETABLE:
        raise BoundaryViolation(
            f"Cannot revert a {model.__name__} creation: the write boundary "
            "has no DELETE there. Correct the row instead."
        )
    instances = list(model.objects.filter(pk__in=entry.target_ids))
    return audited_delete(
        actor,
        instances,
        action=f"revert:{entry.action}",
        reason=reason or f"revert of audit entry {entry.pk}",
    )


def _revert_deletion(actor, entry, model, reason):
    """Reverting a deletion recreates the recorded rows."""
    instances = [model(**values) for values in (entry.before or {}).values()]
    return audited_create(
        actor,
        instances,
        action=f"revert:{entry.action}",
        reason=reason or f"revert of audit entry {entry.pk}",
    )


def set_scope(actor, enrichment_rows, scope, reason=""):
    """Record a human's scope decision on enrichment records.

    The confidence is cleared, not set high. `scope_confidence` is the
    model's estimate of its own answer; a person's answer has no such
    number, and writing 1.0 would make every human correction look like
    the model's most certain prediction wherever confidence is filtered
    or charted.
    """
    return audited_update(
        actor,
        enrichment_rows,
        {"scope": scope, "scope_confidence": None},
        action="review:set_scope",
        reason=reason,
    )
