"""The outlet registry: importing it, and drawing it on a story map.

A story map of newsrooms answers a different question from a story map of
stories. Its dots are outlets, coloured by what each one is -- collected,
not collected, print, replica, social -- and its counties are shaded by how
many outlets are located there. Same shape of payload as `run_story_map`
({points, areas, meta}), so the same renderer draws it; `meta.unit` tells
the renderer what its numbers count.
"""

from __future__ import annotations

import calendar
import csv
import datetime as dt
import io
import re
from pathlib import Path

from django.db import transaction
from django.utils import timezone

#: The points' colours, in the order the legend lists them.
CATEGORIES = ("collected", "not collected", "print", "replica", "social")

#: Where the crawler's builder publishes each rebuild of the registry. A
#: bucket, not the repo: a rebuild is data, and waiting on a pull request to
#: show a newly tagged link or a renamed source is the round trip this
#: replaces. A reviewer's word is an outlet event here, laid over every import.
DEFAULT_URL = "gs://mizzou-news-maps-data/registry/mo_outlet_registry.csv"


def _float(value):
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _int(value):
    try:
        return int(float(value)) if value not in (None, "") else 0
    except ValueError:
        return 0


def read_registry(where=DEFAULT_URL):
    """The registry's rows, from a bucket, a URL or a local path."""
    if str(where).startswith("gs://"):
        from google.cloud import storage

        bucket, _, blob = str(where)[5:].partition("/")
        data = storage.Client().bucket(bucket).blob(blob).download_as_bytes()
        text = data.decode("utf-8-sig")
    elif str(where).startswith(("http://", "https://")):
        import urllib.request

        with urllib.request.urlopen(str(where), timeout=60) as resp:
            text = resp.read().decode("utf-8")
    else:
        text = Path(where).read_text(encoding="utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def import_registry(where=DEFAULT_URL):
    """Replace the table with the registry. Returns counts.

    Whole-file, not incremental: the file IS the registry, so an outlet it no
    longer holds is removed, and every row is written as the file has it.
    """
    from visuals.models import Outlet

    rows = read_registry(where)
    if not rows or "outlet_id" not in rows[0]:
        raise ValueError(f"{where} is not an outlet registry")
    seen = set()
    created = updated = 0
    with transaction.atomic():
        # Three queries for the table -- which ids exist, one insert, one
        # update -- rather than an update_or_create per outlet. Fine at
        # 300 rows; another state's registry is thousands.
        existing = Outlet.objects.in_bulk([r["outlet_id"].strip() for r in rows])
        stamp = timezone.now()
        fresh, changed = [], []
        for r in rows:
            oid = r["outlet_id"].strip()
            if not oid or oid in seen:
                raise ValueError(f"missing or repeated outlet_id: {oid!r}")
            seen.add(oid)
            fields = {
                "source_id": r.get("source_id", ""),
                "name": r.get("outlet", ""),
                "state": (r.get("state") or "MO")[:2],
                "city": r.get("city", ""),
                "county": r.get("county", ""),
                "county_fips": r.get("county_fips", ""),
                "address": r.get("address", ""),
                "lat": _float(r.get("lat")),
                "lon": _float(r.get("lon")),
                "location_basis": r.get("location_basis", ""),
                "host": r.get("host", ""),
                "owner": r.get("owner", ""),
                "status": r.get("status", ""),
                "status_basis": r.get("status_basis", ""),
                "merged_into": r.get("merged_into", ""),
                "aka": r.get("aka", ""),
                "on_map": r.get("map") == "yes",
                "category": r.get("map_category", ""),
                "march_articles": _int(r.get("march_articles")),
                "row": r,
                "imported_at": stamp,
            }
            outlet = existing.get(oid)
            if outlet is None:
                fresh.append(Outlet(outlet_id=oid, **fields))
            else:
                for field, value in fields.items():
                    setattr(outlet, field, value)
                changed.append(outlet)
        Outlet.objects.bulk_create(fresh, batch_size=500)
        if changed:
            Outlet.objects.bulk_update(changed, list(fields), batch_size=500)
        created, updated = len(fresh), len(changed)
        removed, _ = Outlet.objects.exclude(outlet_id__in=seen).delete()
        # The file says what the crawler knew when it was built; an event
        # recorded here since says what happened after. Laid on top, or
        # every import would undo the sales entered between rebuilds.
        from visuals.outlet_events import apply_current

        current = apply_current()
    return {
        "rows": len(rows),
        "created": created,
        "updated": updated,
        "removed": removed,
        "events_applied": current,
    }


# --- the registry on a date ----------------------------------------------------
#
# The registry says what each outlet is now. A map dated March 2026 has to
# draw what was there in March: the Barry County Advertiser, collected that
# month and closed in June, belongs on it; StoneCounty.news, launched in
# August, does not. The outlet events are that history, so a dated map is the
# registry with the events replayed to the date.
#
# The events are sparse, and are meant to fill in as dates are found. So the
# rule leans towards drawing an outlet: a closure or a merger counts only
# once its date has certainly passed ("June 2026" counts from June 30), and a
# launch counts from the earliest day it could have happened. An outlet whose
# dates allow that it was operating is drawn.

#: Events that end an outlet, and events that start (or restart) one.
ENDS = ("closed", "merged")
STARTS = ("launched", "relaunched")


def as_of_period(value):
    """(first day, last day, label) for "2026-03" or "2026-03-15", else None.

    A day stands for its month: whether an outlet was collected is counted
    over the month's articles, and a map is dated by the month it shows.
    Raises ValueError for anything that is neither.
    """
    text = str(value or "").strip()
    if not text:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", text)
    if not m:
        raise ValueError(
            f"As of is a month or a day: 2026-03 or 2026-03-15, not {text!r}."
        )
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"No month {month} in {text!r}.")
    if m.group(3):
        dt.date(year, month, int(m.group(3)))  # a real day, or ValueError
    first = dt.date(year, month, 1)
    last = dt.date(year, month, calendar.monthrange(year, month)[1])
    return first, last, first.strftime("%B %Y")


def _span(event):
    """(earliest, latest) day an event can have happened on."""
    day = event.effective_date
    precision = event.date_precision or "day"
    if precision == "month":
        return day.replace(day=1), day.replace(
            day=calendar.monthrange(day.year, day.month)[1]
        )
    if precision == "year":
        return day.replace(month=1, day=1), day.replace(month=12, day=31)
    if precision == "by":
        return dt.date.min, day
    return day, day


def operating(events, on):
    """Whether an outlet may have been operating on a day, from its events.

    `events` are its dated, unretracted closures, mergers and launches. An
    outlet with a launch among them did not exist before it; one without --
    a relaunch included, which says it existed before -- existed from the
    start of the record.
    """
    timeline = []
    for e in events:
        earliest, latest = _span(e)
        if e.event in ENDS:
            timeline.append((latest, False))
        elif e.event in STARTS:
            timeline.append((earliest, True))
    alive = not any(e.event == "launched" for e in events)
    for when, state in sorted(timeline, key=lambda t: t[0]):
        if when > on:
            break
        alive = state
    return alive


def _dated_events():
    """{outlet_id: [closure, merger and launch events with a date]}."""
    from visuals.models import OutletEvent

    retracted = set(
        OutletEvent.objects.filter(retracts__isnull=False).values_list(
            "retracts_id", flat=True
        )
    )
    out = {}
    for e in OutletEvent.objects.filter(
        event__in=ENDS + STARTS, effective_date__isnull=False
    ).exclude(outlet_id=""):
        if e.pk not in retracted:
            out.setdefault(e.outlet_id, []).append(e)
    return out


def _articles_in(source_ids, first, last):
    """{source id: articles published between two days}, from the corpus."""
    from django.db.models import Count

    from explorer.models import Article

    if not source_ids:
        return {}
    from visuals.corpus import day_range

    start, end = day_range(first, last)
    rows = (
        Article.objects.filter(
            candidate_link__source_id__in=list(source_ids),
            publish_date__gte=start,
            publish_date__lt=end,
        )
        .values("candidate_link__source_id")
        .annotate(n=Count("id"))
    )
    return {r["candidate_link__source_id"]: r["n"] for r in rows}


#: A reviewer's word on what an outlet is, which articles do not overrule:
#: The Dixon Pilot's web side had stories, and it is a replica edition.
REVIEWED_KINDS = ("print", "replica", "social")


def outlets_on(first, last):
    """[(outlet, category, articles)] for a map dated to a month."""
    from visuals.models import Outlet

    events = _dated_events()
    # Drawn today, plus what has closed or merged since: the only outlets
    # that were newsrooms then and are off the map now.
    candidates = [
        o for o in Outlet.objects.order_by("name") if o.on_map or o.status in ENDS
    ]
    counts = _articles_in({o.source_id for o in candidates if o.source_id}, first, last)
    drawn = []
    for o in candidates:
        mine = events.get(o.outlet_id, [])
        n = counts.get(o.source_id, 0) if o.source_id else 0
        was_there = operating(mine, first) or operating(mine, last)
        if not o.on_map:
            # Closed or merged since. Back on the map only where something
            # says it was still going: a dated end after the month began,
            # or articles published that month.
            ended = [_span(e)[1] for e in mine if e.event in ENDS]
            if not (n or (ended and max(ended) >= first)):
                continue
        elif not was_there:
            continue
        if o.category in REVIEWED_KINDS:
            category = o.category
        else:
            category = "collected" if n else "not collected"
        drawn.append((o, category, n))
    return drawn


def run_outlet_map(config=None):
    """{points, areas, meta} for an outlet map (docs/OUTLET_MAP.md).

    A point per outlet on the map, coloured by `category`. A county is shaded
    by how many of the drawn outlets are located in it -- every kind drawn,
    collected or not, since the question is where newsrooms are -- or, when
    the author asks, by how many we collect from, or not at all.

    The author's options are applied here rather than in the browser: an
    outlet with no coordinates is counted in its county but has no dot, so
    the browser cannot recount the shading from the dots it has.
    """
    from visuals.models import Outlet

    config = config or {}
    wanted = {
        c.strip() for c in str(config.get("categories_drawn") or "").split(",")
    } & set(CATEGORIES)
    shade_by = config.get("shade_by") or ""
    period = as_of_period(config.get("as_of"))
    if period:
        first, last, when = period
        drawn = outlets_on(first, last)
        articles_label = f"articles in {when}"
    else:
        drawn = [
            (o, o.category, o.march_articles)
            for o in Outlet.objects.filter(on_map=True).order_by("name")
        ]
        when, articles_label = "", "articles in March 2026"
    if wanted:
        drawn = [d for d in drawn if d[1] in wanted]
    points, by_county = [], {}
    for o, category, articles in drawn:
        counted = shade_by == "" or (
            shade_by == "collected" and category == "collected"
        )
        if o.county_fips and counted:
            by_county[o.county_fips] = by_county.get(o.county_fips, 0) + 1
        if o.lat is None or o.lon is None:
            continue
        points.append(
            {
                "name": o.name,
                "category": category,
                "place": f"{o.city}, {o.state}" if o.city else o.state,
                "county": o.county,
                "geoid": o.county_fips,
                "lat": o.lat,
                "lon": o.lon,
                "owner": o.owner,
                "website": o.host,
                articles_label: articles,
            }
        )
    from visuals.corpus import county_label

    areas = [
        {"geoid": fips, "county name": county_label(fips), "newsrooms": n}
        for fips, n in sorted(by_county.items(), key=lambda kv: -kv[1])
    ]
    meta = {
        "unit": "outlets we collect from" if shade_by == "collected" else "newsrooms",
        "categories": [
            c for c in CATEGORIES if any(p["category"] == c for p in points)
        ],
        "points": len(points),
        "areas": len(areas),
        "newsrooms": sum(by_county.values()),
    }
    if when:
        meta["as_of"] = when
    return {"points": points, "areas": areas, "meta": meta}
