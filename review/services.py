"""The audited write path (SCOPE.md §2.2, boundary §6.5).

Every mutation of crawler data flows through here: the boundary is
checked (mirroring the datadesk_rw column grants — Postgres enforces it
anyway, this makes the refusal a clean error instead of a database
exception), the before/after values are captured, and an append-only
audit entry records actor, targets, and reason. Reverts apply the
`before` values back through the same path as a compensating action —
history is never edited.
"""

from django.db import router, transaction

from audit.models import AuditLogEntry
from explorer.models import Article, ArticleEnrichment, Source


class BoundaryViolation(Exception):
    """A write outside the SCOPE.md §6.5 column boundary."""


# The app-side mirror of infra/sql/create_crawler_write_role.sql.
WRITABLE = {
    Article: ("author", "title", "content", "text", "status", "wire_check_status"),
    ArticleEnrichment: ("skip_reason", "geo_skip_reason"),
    Source: ("canonical_name", "city", "county", "owner", "type"),
}

_BY_TABLE = {model._meta.db_table: model for model in WRITABLE}


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

    pk_name = model._meta.pk.name
    before = {
        str(getattr(obj, pk_name)): {field: getattr(obj, field) for field in changes}
        for obj in instances
    }

    write_alias = router.db_for_write(model)
    with transaction.atomic(using=write_alias):
        for obj in instances:
            for field, value in changes.items():
                setattr(obj, field, value)
            obj.save(using=write_alias, update_fields=list(changes))
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

    write_alias = router.db_for_write(model)
    pk_name = model._meta.pk.name
    missing = []
    reverted_ids = []
    before = {}

    with transaction.atomic(using=write_alias):
        for pk, values in (entry.before or {}).items():
            obj = model.objects.filter(**{pk_name: pk}).first()
            if obj is None:
                missing.append(pk)
                continue
            before[pk] = {field: getattr(obj, field) for field in values}
            for field, value in values.items():
                setattr(obj, field, value)
            obj.save(using=write_alias, update_fields=list(values))
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
