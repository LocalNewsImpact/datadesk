"""Reviewer corrections to the newspaper history's catalogues
(docs/NEWSPAPER_HISTORY.md): what each kind must name, recording many at once,
and the files the history's build reads, written from these tables.
"""

import csv
import re
from pathlib import Path

from django.db import transaction

from audit.models import AuditLogEntry
from visuals.models import HistoryCorrection, Outlet, OutletEvent

H = HistoryCorrection

#: The columns of a correction, in the order the build's file carries them.
FIELDS = (
    "kind",
    "title",
    "county",
    "town",
    "field",
    "value",
    "other_title",
    "other_county",
    "other_town",
    "evidence",
)

#: What each kind must name. A link with no later title is not a link.
REQUIRED = {
    H.SET: ("county", "field", "value"),
    H.DROP: ("title", "county"),
    H.REELS: ("title", "county", "other_title", "other_county", "field", "value"),
    H.MOVED: ("title", "value"),
    H.LINK: ("title", "county", "other_title", "other_county"),
    H.UNLINK: ("title", "county", "other_title", "other_county"),
    H.ALIAS: ("title", "county", "other_title", "other_town"),
    H.NOT_A_NEWSROOM: ("title",),
    H.NOT_THE_TOWNS_PAPER: ("title",),
    H.PRINT_ENDED: ("title", "field", "value"),
    H.RETRACTION: (),
}

#: The catalogue fields a `set` may correct, and the shape of each value.
SETTABLE = {
    "town": None,
    "county": None,
    "start_year": r"\d{4}",
    "end_year": r"\d{4}",
    "held_first": r"\d{4}-\d{2}-\d{2}",
    "held_last": r"\d{4}-\d{2}-\d{2}",
}


def problems(c):
    """What is wrong with a correction (a model instance), as sentences."""
    text = {k: str(getattr(c, k) or "").strip() for k in FIELDS}
    kinds = dict(H.KINDS)
    if text["kind"] not in kinds:
        return [f"Say what kind of correction: one of {', '.join(kinds)}."]
    errors = [
        f"A {text['kind']} correction needs {f}."
        for f in REQUIRED[text["kind"]]
        if not text[f]
    ]
    if not text["evidence"]:
        errors.append("Say where this came from: the evidence.")
    kind, field, value = text["kind"], text["field"], text["value"]
    if kind == H.SET:
        if not (text["title"] or text["town"]):
            errors.append("A set correction names the entry: a title or a town.")
        if field and field not in SETTABLE:
            errors.append(f"A set correction changes one of {', '.join(SETTABLE)}.")
        elif field and SETTABLE[field] and not re.fullmatch(SETTABLE[field], value):
            errors.append(f"{field} is {SETTABLE[field]!r}, not {value!r}.")
    if kind == H.REELS and field != "end_year":
        errors.append("The reels' paper takes their last year: field end_year.")
    is_year = kind in (H.SET, H.REELS) and field.endswith("_year") and value
    if is_year and not re.fullmatch(r"\d{4}", value):
        errors.append(f"A year is four digits, not {value!r}.")
    if kind == H.MOVED and value and len(value.split(" > ")) < 2:
        errors.append('A move names two or more towns, " > " between them.')
    if kind == H.PRINT_ENDED:
        if field and field not in ("merged", "closed"):
            errors.append("A print run ended merged or closed.")
        if value and not re.fullmatch(r"\d{4}", value):
            errors.append(f"A print run ends in a year, not {value!r}.")
    if kind == H.RETRACTION and c.retracts_id is None:
        errors.append("A retraction names the correction it withdraws.")
    return errors


def live():
    """The corrections in force: not retractions, and not retracted."""
    retracted = set(
        H.objects.filter(retracts__isnull=False).values_list("retracts_id", flat=True)
    )
    return [
        c
        for c in H.objects.exclude(kind=H.RETRACTION).order_by("recorded_at", "id")
        if c.pk not in retracted
    ]


def load(actor, rows, origin="backfill"):
    """Record many corrections. Returns (recorded, skipped, errors).

    A row identical to a correction in force is skipped, so a file can be
    loaded twice. A file with any bad row writes nothing and says which lines.
    """
    built, errors = [], []
    for n, row in enumerate(rows, start=2):
        c = H(**{k: str(row.get(k) or "").strip() for k in FIELDS})
        bad = problems(c)
        if bad:
            errors.append(f"line {n}: {'; '.join(bad)}")
        built.append(c)
    if errors:
        return 0, 0, errors
    have = {tuple(getattr(c, k) for k in FIELDS) for c in live()}
    recorded = skipped = 0
    with transaction.atomic():
        for c in built:
            key = tuple(getattr(c, k) for k in FIELDS)
            if key in have:
                skipped += 1
                continue
            have.add(key)
            c.recorded_by, c.origin = actor, origin
            c.save()
            recorded += 1
        if recorded:
            AuditLogEntry.objects.create(
                actor=actor,
                action="history_correction:load",
                target_table=H._meta.db_table,
                target_ids=[],
                before=None,
                after={"recorded": recorded, "skipped": skipped},
                reason=origin,
            )
    return recorded, skipped, []


def _unretracted_events(**filters):
    retracted = set(
        OutletEvent.objects.filter(retracts__isnull=False).values_list(
            "retracts_id", flat=True
        )
    )
    return [
        e
        for e in OutletEvent.objects.filter(**filters).order_by("recorded_at", "id")
        if e.pk not in retracted
    ]


def export(out):
    """Write the build's reviewer inputs into the folder `out`. Returns
    {file name: rows written}.

    reviewer_corrections.csv -- the corrections in force.
    registry_founded.csv -- each outlet's launch, from its `launched` events:
        the year a newsroom the catalogue never filmed was founded.
    datadesk_merged_events.csv -- each merger: `merged` events and `status`
        events that say merged.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written = {}

    with open(out / "reviewer_corrections.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(FIELDS)
        rows = live()
        w.writerows([getattr(c, k) for k in FIELDS] for c in rows)
        written["reviewer_corrections.csv"] = len(rows)

    outlets = {o.outlet_id: o for o in Outlet.objects.all()}
    founded = {}
    for e in _unretracted_events(event=OutletEvent.LAUNCHED):
        if e.effective_date is None:
            continue
        o = outlets.get(e.outlet_id)
        # the latest word on an outlet's launch stands
        founded[e.outlet_id or e.outlet_name] = (
            e.outlet_name if o is None else o.name,
            "" if o is None else o.city,
            "" if o is None else o.county,
            e.effective_date.year,
            e.note or e.evidence_url,
        )
    with open(out / "registry_founded.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["outlet", "city", "county", "founded_year", "source_for_dates"])
        w.writerows(founded.values())
        written["registry_founded.csv"] = len(founded)

    merged = [
        e
        for e in _unretracted_events(event__in=[OutletEvent.MERGED, OutletEvent.STATUS])
        if e.event == OutletEvent.MERGED or e.new_status == "merged"
    ]
    with open(out / "datadesk_merged_events.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(
            [
                "outlet_id",
                "outlet_name",
                "effective_date",
                "date_precision",
                "merged_into",
                "evidence_url",
                "note",
            ]
        )
        w.writerows(
            [
                e.outlet_id or "",
                e.outlet_name,
                e.effective_date or "",
                e.date_precision,
                e.merged_into,
                e.evidence_url,
                (e.note or "")[:200],
            ]
            for e in merged
        )
        written["datadesk_merged_events.csv"] = len(merged)
    return written
