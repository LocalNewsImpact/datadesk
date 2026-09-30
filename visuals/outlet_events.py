"""Recording what happened to an outlet, and laying it over the registry.

The registry says what an outlet is now; `OutletEvent` is how it got there
(docs/OUTLET_EVENTS.md). Everything that writes an event comes through here
-- the form, the backfill, and every owner edit `audited_update` makes -- so
there is one set of rules for what an event must carry.
"""

import csv
import datetime as dt
import io
from urllib.request import urlopen

from django.db import transaction

from audit.models import AuditLogEntry
from visuals.models import Outlet, OutletEvent

E = OutletEvent

#: What each kind of event must name, beyond the outlet. A sale with no buyer
#: is not a sale anybody can draw.
REQUIRED = {
    E.SOLD: ("to_owner",),
    E.OWNER_CHANGED: ("to_owner",),
    E.MERGED: ("merged_into",),
    E.RENAMED: ("new_name",),
    E.STATUS: ("new_status",),
}

#: Fields an event is compared on when a backfill asks whether it is already
#: recorded. Not the note: rewording a note is not a second sale.
IDENTITY = (
    "outlet_id",
    "outlet_name",
    "event",
    "effective_date",
    "from_owner",
    "to_owner",
    "merged_into",
    "new_name",
    "new_status",
    "evidence_url",
)

FIELDS = (
    "outlet_id",
    "outlet_name",
    "event",
    "effective_date",
    "date_precision",
    "from_owner",
    "to_owner",
    "merged_into",
    "new_name",
    "new_status",
    "evidence_url",
    "note",
    "sets_current",
)


class EventError(ValueError):
    """An event that cannot be recorded, with every reason why."""

    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


def clean(data):
    """The fields of one event, typed and trimmed, and what is wrong with it.

    `data` is a form, a CSV row or a dict. Returns (fields, errors).
    """
    text = {k: str(data.get(k) or "").strip() for k in FIELDS if k != "sets_current"}
    errors = []
    kinds = dict(E.EVENTS)
    if text["event"] not in kinds:
        errors.append(f"Say what happened: one of {', '.join(kinds)}.")
    if not text["outlet_name"]:
        errors.append("Name the outlet.")
    for field in REQUIRED.get(text["event"], ()):
        if not text[field]:
            errors.append(f"A {kinds[text['event']].lower()} event needs {field}.")
    statuses = dict(E.STATUSES)
    if text["event"] == E.STATUS:
        if text["new_status"] not in statuses:
            errors.append(f"Say what the outlet is: one of {', '.join(statuses)}.")
        elif text["new_status"] in ("merged", "duplicate") and not text["merged_into"]:
            errors.append(
                f"A {text['new_status']} outlet needs merged_into: "
                "what it is part of."
            )
    else:
        text["new_status"] = ""
    when = None
    if text["effective_date"]:
        try:
            when = dt.date.fromisoformat(text["effective_date"])
        except ValueError:
            errors.append("The date is YYYY-MM-DD.")
    precisions = dict(E.PRECISIONS)
    if text["date_precision"] and text["date_precision"] not in precisions:
        errors.append(f"Precision is one of {', '.join(precisions)}.")
    if when and not text["date_precision"]:
        text["date_precision"] = E.DAY
    if not when:
        text["date_precision"] = ""
    if when and when > dt.date.today():
        # A planned closure is news, not history. It is recorded when it
        # happens, or the timeline shows a paper closed that is printing.
        errors.append("An event is recorded once it has happened.")
    if text["event"] != E.CORRECTION and not (text["evidence_url"] or text["note"]):
        errors.append("Say where this came from: a link or a note.")
    fields = {**text, "effective_date": when}
    sets = data.get("sets_current")
    fields["sets_current"] = str(sets).strip().lower() in ("1", "true", "yes", "on")
    return fields, errors


def record(actor, data, origin="form", retracts=None):
    """Write one event and apply it. Raises EventError if it is not one."""
    if retracts is not None:
        # The outlet is the one the withdrawn event was about; only the
        # reason is the retraction's own.
        data = {
            "note": data.get("note", ""),
            "event": E.RETRACTION,
            "outlet_id": retracts.outlet_id,
            "outlet_name": retracts.outlet_name,
        }
    fields, errors = clean(data)
    if retracts is None and fields["event"] == E.RETRACTION:
        errors.append("A retraction names the event it withdraws.")
    if errors:
        raise EventError(errors)
    with transaction.atomic():
        if retracts is not None:
            # Under the withdrawn event's row lock: checked outside the
            # transaction, two retractions of one event both found it
            # unretracted and both were recorded.
            OutletEvent.objects.select_for_update().get(pk=retracts.pk)
            if retracts.retracted_by.exists():
                raise EventError(["That event is already retracted."])
        event = OutletEvent.objects.create(
            **fields, retracts=retracts, recorded_by=actor, origin=origin
        )
        AuditLogEntry.objects.create(
            actor=actor,
            action="outlet_event:record",
            target_table=OutletEvent._meta.db_table,
            target_ids=[str(event.pk)],
            before=None,
            after={k: str(v) for k, v in fields.items()},
            reason=f"{origin}: {event}",
        )
    _write_through(actor, event)
    if event.outlet_id:
        apply_current([event.outlet_id])
    return event


def _write_through(actor, event):
    """An owner change on an outlet we crawl, written to `sources.owner` too.

    So the crawler's record, and the registry rebuilt from it, say what the
    event says. Through `audited_update`, with the event hook off: this write
    is the event, not a second one.
    """
    if not (event.sets_current and event.to_owner and event.outlet_id):
        return
    outlet = Outlet.objects.filter(outlet_id=event.outlet_id).first()
    if outlet is None or not outlet.source_id:
        return
    from explorer.models import Source
    from review.services import audited_update

    source = Source.objects.filter(pk=outlet.source_id).first()
    if source is None or (source.owner or "") == event.to_owner:
        return
    audited_update(
        actor,
        [source],
        {"owner": event.to_owner},
        action="source:edit",
        reason=f"outlet event {event.pk}: {event}",
        events=False,
    )


#: What a current event sets on the registry row.
def _apply(outlet, event):
    if event.to_owner and event.event in (
        E.SOLD,
        E.OWNER_CHANGED,
        E.CORRECTION,
        E.RELAUNCHED,
    ):
        outlet.owner = event.to_owner
    if event.event == E.MERGED:
        outlet.status = "merged"
        outlet.merged_into = event.merged_into
        outlet.on_map = False
    elif event.event == E.CLOSED:
        outlet.status = "closed"
        outlet.on_map = False
    elif event.event == E.RELAUNCHED:
        outlet.status = "active"
    if event.event == E.RENAMED:
        outlet.name = event.new_name
    if event.event == E.STATUS:
        outlet.status = event.new_status
        outlet.merged_into = (
            event.merged_into if event.new_status in ("merged", "duplicate") else ""
        )
        outlet.status_basis = event.note or dict(E.STATUSES)[event.new_status]
        outlet.category, outlet.on_map = _placed(outlet, event.new_status)


#: Statuses drawn on the outlet map, each in its own colour. Every other
#: status is off the map: a legal-notice sheet or a shopper is not a
#: newsroom, and a merged or closed outlet is drawn where it went, if at all.
DRAWN = ("replica", "print", "social")
LISTED = ("legal", "business", "shopper", "magazine")


def _placed(outlet, status):
    """(category, on_map) for an outlet a reviewer gave `status`."""
    if status == "active":
        return ("collected" if outlet.march_articles else "not collected"), True
    if status in DRAWN:
        return status, True
    if status in LISTED:
        return status, False
    return "", False


#: The registry-row fields an event may set, and the file's column for each.
_FROM_FILE = {
    "owner": "owner",
    "status": "status",
    "status_basis": "status_basis",
    "merged_into": "merged_into",
    "name": "outlet",
    "category": "map_category",
}


def apply_current(outlet_ids=None):
    """Lay every standing current event over its outlet's registry row.

    Each row is first reset to what the imported file says, so a retracted
    event stops showing rather than lingering until the next import. Then
    the events are applied in the order they were recorded: the last word
    is the latest one entered, which is the reviewer's latest knowledge.
    """
    rows = Outlet.objects.all()
    if outlet_ids is not None:
        rows = rows.filter(outlet_id__in=list(outlet_ids))
    retracted = set(
        OutletEvent.objects.filter(retracts__isnull=False).values_list(
            "retracts_id", flat=True
        )
    )
    events = {}
    for event in OutletEvent.objects.filter(sets_current=True).order_by(
        "recorded_at", "id"
    ):
        if event.pk not in retracted and event.event != E.RETRACTION:
            events.setdefault(event.outlet_id, []).append(event)
    changed = 0
    for outlet in rows:
        mine = events.get(outlet.outlet_id, [])
        was = {f: getattr(outlet, f) for f in (*_FROM_FILE, "on_map")}
        file = outlet.row or {}
        for field, column in _FROM_FILE.items():
            if column in file:
                setattr(outlet, field, file.get(column) or "")
        if "map" in file:
            outlet.on_map = file.get("map") == "yes"
        for event in mine:
            _apply(outlet, event)
        now = {f: getattr(outlet, f) for f in was}
        if now != was:
            outlet.save(update_fields=list(was))
            changed += 1
    return changed


def record_owner_edits(actor, before, changes, entry):
    """An `owner_changed` event for each source whose owner an edit changed.

    Called by `audited_update` for every write to `sources`, so an owner is
    never overwritten without a trace, whatever page wrote it. The date is
    unknown: an edit says what the owner is, not when that became true.
    """
    if "owner" not in changes:
        return []
    from explorer.models import Source

    made = []
    new = changes["owner"] or ""
    for pk, values in before.items():
        old = (values or {}).get("owner") or ""
        if old == new:
            continue
        source = Source.objects.filter(pk=pk).first()
        name = (source.canonical_name or source.host) if source else pk
        made.append(
            OutletEvent.objects.create(
                outlet_id=pk,
                outlet_name=name or pk,
                event=E.OWNER_CHANGED,
                from_owner=old,
                to_owner=new,
                note=(entry.reason or "")[:1000],
                sets_current=False,
                recorded_by=actor,
                origin=f"source edit (audit {entry.pk})",
            )
        )
    return made


def read_rows(where):
    """The rows of an events CSV at a path or URL."""
    if str(where).startswith(("http://", "https://")):
        with urlopen(where) as response:  # noqa: S310 -- an operator's URL
            text = response.read().decode("utf-8-sig")
    else:
        with open(where, encoding="utf-8-sig") as fh:
            text = fh.read()
    return list(csv.DictReader(io.StringIO(text)))


def load(actor, rows, origin="backfill"):
    """Record many events. Returns (recorded, skipped, errors).

    A row identical to an event already recorded is skipped, so the same
    file can be loaded twice. A row with errors is reported with its line
    number and nothing from the file is written: half a backfill is a
    history with holes nobody can see.
    """
    cleaned, errors = [], []
    for n, row in enumerate(rows, start=2):
        fields, problems = clean(row)
        if problems:
            errors.append(f"line {n}: {'; '.join(problems)}")
        cleaned.append(fields)
    if errors:
        return 0, 0, errors
    have = {
        tuple(str(v) for v in key) for key in OutletEvent.objects.values_list(*IDENTITY)
    }
    recorded = skipped = 0
    touched = set()
    with transaction.atomic():
        for fields in cleaned:
            key = tuple(
                str(fields[k]) if fields[k] is not None else "None" for k in IDENTITY
            )
            if key in have:
                skipped += 1
                continue
            have.add(key)
            event = OutletEvent.objects.create(
                **fields, recorded_by=actor, origin=origin
            )
            recorded += 1
            if event.outlet_id and event.sets_current:
                touched.add(event.outlet_id)
        if recorded:
            AuditLogEntry.objects.create(
                actor=actor,
                action="outlet_event:load",
                target_table=OutletEvent._meta.db_table,
                target_ids=[],
                before=None,
                after={"recorded": recorded, "skipped": skipped},
                reason=origin,
            )
    if touched:
        apply_current(touched)
    return recorded, skipped, []
