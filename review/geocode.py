"""Where a typed address is, for the geography coder's map.

A way to move the map, never an answer: the coder still drops the pin,
and the pin is what is resolved and recorded (`geo_resolve`).

The Census geocoder first: it is the source the boundaries come from,
and it matches a house number or an intersection ("Providence Rd and
Broadway, Columbia MO") exactly. It knows nothing else -- a bare street
or a landmark ("Arrowhead Stadium") finds nothing -- so OpenStreetMap's
Nominatim is asked next. Nominatim allows one request a second and asks
for an identifying User-Agent; the map asks only when the coder presses
Enter, and every answer is cached for a day.

Neither service sends CORS headers, so the browser cannot ask them
itself; the server does.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request

from django.core.cache import cache

CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
AGENT = "datadesk-geography-coding/1.0 (+https://datadesk.localnewsimpact.org)"
TIMEOUT = 8
CACHE_SECONDS = 24 * 60 * 60
#: How close the map goes to an address the Census matched.
ADDRESS_ZOOM = 17


def _get(url, params):
    request = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
        return json.load(response)


def census(typed):
    data = _get(
        CENSUS, {"address": typed, "benchmark": "Public_AR_Current", "format": "json"}
    )
    return [
        {
            "label": match["matchedAddress"],
            "lat": float(match["coordinates"]["y"]),
            "lon": float(match["coordinates"]["x"]),
            "zoom": ADDRESS_ZOOM,
            "source": "Census",
        }
        for match in data["result"]["addressMatches"][:5]
    ]


def openstreetmap(typed):
    # One request a second, across every worker: wait out another's.
    for _ in range(3):
        if cache.add("geocode:nominatim-lock", 1, 1):
            break
        time.sleep(1)
    rows = _get(
        NOMINATIM,
        {"q": typed, "format": "jsonv2", "countrycodes": "us", "limit": 5},
    )
    out = []
    for row in rows:
        hit = {
            "label": row["display_name"],
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "source": "OpenStreetMap",
        }
        # [south, north, west, east]: a town or a county is shown whole.
        box = row.get("boundingbox")
        if box and len(box) == 4:
            south, north, west, east = (float(x) for x in box)
            hit["bbox"] = [west, south, east, north]
        out.append(hit)
    return out


def look_up(typed):
    """[{label, lat, lon, zoom or bbox, source}], best first; [] where
    nothing matched. A service that failed is not cached as an empty
    answer, so the next try asks again."""
    typed = " ".join(typed.split())[:200]
    key = "geocode:" + hashlib.sha256(typed.lower().encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    failed = False
    results = []
    for source in (census, openstreetmap):
        try:
            results = source(typed)
        except (OSError, ValueError, KeyError, TypeError):
            failed = True
            results = []
        if results:
            break
    if results or not failed:
        cache.set(key, results, CACHE_SECONDS)
    return results
