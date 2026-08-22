"""GEOID rollups (SCOPE.md §2.4 geography).

A place GEOID is state(2) + place(5) and carries no county code, but the
Census publishes a place-to-county crosswalk, so the county is a lookup
rather than a slice. `datasets/data/place_to_county.json` is that file
(national_place_by_county2020), mapping each place GEOID to its primary
county and how many counties it touches — 4.1% of places straddle more
than one, and those are assigned to the first county the Census lists.
"""

import csv
import json
import re
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


# --- county names -----------------------------------------------------------
#
# The Census county gazetteer, vendored from the crawler so both systems
# agree on what a county is called. Names carry a type suffix ("Boone
# County", "St. Louis city", "Ste. Genevieve County"); matching strips it
# and folds punctuation, so "St Louis", "St. Louis" and "SAINT LOUIS
# COUNTY" all resolve to the same record.

_COUNTIES = Path(__file__).resolve().parent / "data" / "census_counties.csv"
_counties = None

# "city" is deliberately absent: Missouri's St. Louis city (29510) and
# St. Louis County (29189) are different places, and Virginia has dozens
# of independent cities named for the county beside them. Folding away
# "city" would silently merge them, so a bare "St. Louis" resolves to the
# county and "St. Louis city" to the city.
_COUNTY_SUFFIX = re.compile(
    r"\s+(county|parish|borough|census area|municipality|municipio|"
    r"city and borough|planning region)$",
    re.IGNORECASE,
)


def _fold(name):
    """Fold a county name for matching: no punctuation, no suffix, no case."""
    text = (name or "").strip().lower()
    text = text.replace(".", "").replace(",", " ")
    # "Saint" and "Sainte" are written "St." and "Ste." in the gazetteer.
    text = re.sub(r"\bsainte\b", "ste", text)
    text = re.sub(r"\bsaint\b", "st", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _COUNTY_SUFFIX.sub("", text).strip()


def _load_counties():
    global _counties
    if _counties is None:
        table = {}
        with open(_COUNTIES, newline="") as fh:
            for row in csv.DictReader(fh):
                table[(row["USPS"], _fold(row["NAME"]))] = (
                    row["GEOID"],
                    row["NAME"],
                )
        _counties = table
    return _counties


def canonical_county(state, name):
    """(fips, canonical_name) for a county in a state, or (None, None).

    The canonical name is the gazetteer's own spelling with its type
    suffix removed — "St. Louis", "Ste. Genevieve", "DeKalb" — which is
    what a normalization pass should write back.
    """
    entry = _load_counties().get((state_code(state), _fold(name)))
    if entry is None:
        return None, None
    fips, official = entry
    return fips, _COUNTY_SUFFIX.sub("", official).strip()


def suggest_counties(state, name, limit=3):
    """Close gazetteer names for a value that did not match."""
    import difflib

    pool = [k[1] for k in _load_counties() if k[0] == state_code(state)]
    matches = difflib.get_close_matches(_fold(name), pool, n=limit, cutoff=0.72)
    return [
        canonical_county(state, m)[1] for m in matches if canonical_county(state, m)[1]
    ]


# --- state codes ------------------------------------------------------------
#
# Source records carry the state as "MO" or "Missouri" depending on when
# they were loaded, so every lookup normalizes first.

_STATE_BY_NAME = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "puerto rico": "PR",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}


def state_code(value):
    """USPS code for a state written either way, or ''."""
    text = (value or "").strip()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return _STATE_BY_NAME.get(text.lower(), "")


def states_with_county(name):
    """Every state whose gazetteer has a county by this name."""
    folded = _fold(name)
    return sorted({st for st, key in _load_counties() if key == folded})


def place_county(state, name):
    """(place, county fips, county name) when the value names a place.

    A source whose county field reads "Kansas City" has a city in it;
    naming the county that place sits in turns a dead end into a
    correction.
    """
    from datasets.places import place_geoid

    geoid = place_geoid(state_code(state), name)
    if not geoid:
        return None
    county_fips = county_for_place(geoid)[0]
    if not county_fips:
        return None
    for (_st, _key), (fips, official) in _load_counties().items():
        if fips == county_fips:
            return name, county_fips, _COUNTY_SUFFIX.sub("", official).strip()
    return None
