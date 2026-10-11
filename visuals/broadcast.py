"""Broadcast stations whose news we collect (docs/NEWSPAPER_HISTORY.md):
reading a station's dates and call letters from a review sheet, and the rows
the newspaper history's build reads.
"""

import datetime as dt
import re

from django.db import transaction

from audit.models import AuditLogEntry
from visuals.models import BroadcastStation, CallSign

S = BroadcastStation

#: The columns of a station in a sheet or in the build's file.
FIELDS = (
    "facility_id",
    "service",
    "city",
    "state",
    "outlet_id",
    "first_air_date",
    "call_signs",
    "evidence",
    "evidence_url",
    "note",
)


def parse_date(text):
    """(date, precision) for "1953", "1971-02" or "1953-12-21"; (None, "")
    for blank. Raises ValueError for anything else."""
    text = str(text or "").strip()
    if not text:
        return None, ""
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", text)
    if not m:
        raise ValueError(f"A date is 1953, 1953-12 or 1953-12-21, not {text!r}.")
    year, month, day = (int(g) if g else None for g in m.groups())
    if day:
        return dt.date(year, month, day), S.DAY
    if month:
        return dt.date(year, month, 1), S.MONTH
    return dt.date(year, 1, 1), S.YEAR


def parse_call_signs(text):
    """[(call sign, start year, end year)] from "KCBJ-TV 1971-1985; KMIZ 1985-".
    The one with no end year is the station's call letters now."""
    out = []
    for part in [p.strip() for p in str(text or "").split(";") if p.strip()]:
        m = re.fullmatch(r"(\S+)\s+(\d{4})?\s*-\s*(\d{4})?", part)
        if not m:
            raise ValueError(f"A call sign is 'KMIZ 1985-', not {part!r}.")
        call, start, end = m.groups()
        out.append((call, int(start) if start else None, int(end) if end else None))
    if sum(1 for _, _, end in out if end is None) != 1:
        raise ValueError("Exactly one call sign is current: no end year.")
    return out


def format_call_signs(station):
    return "; ".join(
        f"{c.call_sign} {c.start_year or ''}-{c.end_year or ''}"
        for c in station.call_signs.all()
    )


def problems(row):
    """What is wrong with a station row, as sentences."""
    text = {k: str(row.get(k) or "").strip() for k in FIELDS}
    errors = [
        f"A station needs {f}."
        for f in ("facility_id", "service", "city", "outlet_id", "evidence")
        if not text[f]
    ]
    if text["service"] and text["service"] not in dict(S.SERVICES):
        errors.append(f"The service is one of {', '.join(dict(S.SERVICES))}.")
    for parse, field in (
        (parse_date, "first_air_date"),
        (parse_call_signs, "call_signs"),
    ):
        try:
            parse(text[field])
        except ValueError as e:
            errors.append(str(e))
    return errors


def load(actor, rows, origin="backfill"):
    """Record or update stations by facility id. Returns (written, errors).

    A sheet with any bad row writes nothing and says which lines. A station
    already recorded takes the sheet's values, its call signs included.
    """
    errors = []
    for n, row in enumerate(rows, start=2):
        bad = problems(row)
        if bad:
            errors.append(f"line {n}: {'; '.join(bad)}")
    if errors:
        return 0, errors
    with transaction.atomic():
        for row in rows:
            text = {k: str(row.get(k) or "").strip() for k in FIELDS}
            when, precision = parse_date(text["first_air_date"])
            station, _ = S.objects.update_or_create(
                facility_id=text["facility_id"],
                defaults={
                    "service": text["service"],
                    "city": text["city"],
                    "state": text["state"] or "MO",
                    "outlet_id": text["outlet_id"],
                    "first_air_date": when,
                    "date_precision": precision,
                    "evidence": text["evidence"],
                    "evidence_url": text["evidence_url"],
                    "note": text["note"],
                },
            )
            station.call_signs.all().delete()
            CallSign.objects.bulk_create(
                CallSign(station=station, call_sign=c, start_year=a, end_year=b)
                for c, a, b in parse_call_signs(text["call_signs"])
            )
        AuditLogEntry.objects.create(
            actor=actor,
            action="broadcast_station:load",
            target_table=S._meta.db_table,
            target_ids=[str(r.get("facility_id")) for r in rows],
            before=None,
            after={"written": len(rows)},
            reason=origin,
        )
    return len(rows), []


def rows_for_export():
    """One row per station, as the build reads it."""
    out = []
    for s in S.objects.prefetch_related("call_signs").order_by("facility_id"):
        if s.first_air_date is None:
            when = ""
        elif s.date_precision == S.YEAR:
            when = str(s.first_air_date.year)
        elif s.date_precision == S.MONTH:
            when = s.first_air_date.strftime("%Y-%m")
        else:
            when = s.first_air_date.isoformat()
        out.append(
            {
                "facility_id": s.facility_id,
                "service": s.service,
                "city": s.city,
                "state": s.state,
                "outlet_id": s.outlet_id,
                "first_air_date": when,
                "call_signs": format_call_signs(s),
                "evidence": s.evidence,
                "evidence_url": s.evidence_url,
                "note": s.note,
            }
        )
    return out
