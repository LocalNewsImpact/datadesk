"""The outlet registry: importing it, and drawing it on a story map.

A story map of newsrooms answers a different question from a story map of
stories. Its dots are outlets, coloured by what each one is -- collected,
not collected, print, replica, social -- and its counties are shaded by how
many outlets are located there. Same shape of payload as `run_story_map`
({points, areas, meta}), so the same renderer draws it; `meta.unit` tells
the renderer what its numbers count.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from django.db import transaction

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
            }
            _, made = Outlet.objects.update_or_create(outlet_id=oid, defaults=fields)
            created += made
            updated += not made
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
    drawn = Outlet.objects.filter(on_map=True).order_by("name")
    if wanted:
        drawn = drawn.filter(category__in=wanted)
    points, by_county = [], {}
    for o in drawn:
        counted = shade_by == "" or (
            shade_by == "collected" and o.category == "collected"
        )
        if o.county_fips and counted:
            by_county[o.county_fips] = by_county.get(o.county_fips, 0) + 1
        if o.lat is None or o.lon is None:
            continue
        points.append(
            {
                "name": o.name,
                "category": o.category,
                "place": f"{o.city}, {o.state}" if o.city else o.state,
                "county": o.county,
                "geoid": o.county_fips,
                "lat": o.lat,
                "lon": o.lon,
                "owner": o.owner,
                "website": o.host,
                "articles in March 2026": o.march_articles,
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
    return {"points": points, "areas": areas, "meta": meta}
