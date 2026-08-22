"""GEOID rollups (SCOPE.md §2.4 geography).

A place GEOID is state(2) + place(5) and carries no county code, but the
Census publishes a place-to-county crosswalk, so the county is a lookup
rather than a slice. `datasets/data/place_to_county.json` is that file
(national_place_by_county2020), mapping each place GEOID to its primary
county and how many counties it touches — 4.1% of places straddle more
than one, and those are assigned to the first county the Census lists.
"""

import json
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "place_to_county.json"
_crosswalk = None


def _load():
    global _crosswalk
    if _crosswalk is None:
        with open(_DATA) as fh:
            _crosswalk = json.load(fh)
    return _crosswalk


def county_for_place(place_geoid):
    """(county_fips, county_count) for a place GEOID, or (None, 0)."""
    entry = _load().get((place_geoid or "").strip())
    return (entry[0], entry[1]) if entry else (None, 0)


def to_county(geoid, level):
    """County FIPS for any point coding, or None where undecidable.

    county/tract/block GEOIDs begin with state+county; place GEOIDs go
    through the crosswalk; a state coding has no county.
    """
    if not geoid:
        return None
    if level in ("county", "tract", "block"):
        return geoid[:5] if len(geoid) >= 5 else None
    if level == "place":
        return county_for_place(geoid)[0]
    return None


def to_state(geoid, level=None):
    """State FIPS — the first two digits of any GEOID coding."""
    return geoid[:2] if geoid and len(geoid) >= 2 else None
