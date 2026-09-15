"""City validation against the Census place gazetteer (SCOPE.md §2.5) —
catching Grenfield/Kirskville-class typos at entry.

The gazetteer is the crawler's own bundled file
(MizzouNewsCrawler src/enrichment/data/census_places.csv), vendored, so
both systems agree on what a place is. Names carry an LSAD suffix
("Columbia city", "Whiteside village"); matching strips it, as the
crawler's fips.py does.
"""

import csv
import difflib
import re
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "census_places.csv"

# The LSAD descriptors appearing as name suffixes.
_SUFFIX = re.compile(
    r"\s+(city|town|village|borough|municipality|CDP|"
    r"comunidad|zona urbana|urbana|consolidated government|"
    r"metro government|metropolitan government|unified government|"
    r"city and borough)$",
    re.IGNORECASE,
)

_places: dict[str, set[str]] | None = None
_geoids: dict[tuple[str, str], str] | None = None
_labels: dict[str, str] | None = None


def _norm(name):
    return re.sub(r"\s+", " ", name).strip().lower()


def _load():
    global _places, _geoids, _labels
    if _places is not None:
        return _places
    places: dict[str, set[str]] = {}
    geoids: dict[tuple[str, str], str] = {}
    labels: dict[str, str] = {}
    with open(_DATA, newline="") as fh:
        for row in csv.DictReader(fh):
            bare = _SUFFIX.sub("", row["NAME"]).strip()
            name = _norm(bare)
            places.setdefault(row["USPS"], set()).add(name)
            # First (lowest GEOID) wins on bare-name ties, matching the
            # crawler's fips.py.
            geoids.setdefault((row["USPS"], name), row["GEOID"])
            # Reverse: the gazetteer's own casing, suffix stripped, with
            # the state alongside because place names repeat across lines
            # (there is a Springfield in most of them).
            labels[row["GEOID"]] = f"{bare}, {row['USPS']}"
    _places = places
    _geoids = geoids
    _labels = labels
    return places


def place_geoid(state, city):
    """The place GEOID for a city in a state, or None."""
    _load()
    return _geoids.get(((state or "").strip().upper(), _norm(city or "")))


def validate_city(state, city):
    """(is_known, suggestions) for a city in a state.

    Unknown state → (False, []). Suggestions are the gazetteer's closest
    names, for the form's "did you mean" line.
    """
    state_places = _load().get((state or "").strip().upper())
    if not state_places:
        return False, []
    name = _norm(city or "")
    if name in state_places:
        return True, []
    suggestions = difflib.get_close_matches(name, state_places, n=3, cutoff=0.75)
    return False, [s.title() for s in suggestions]


def place_label(geoid):
    """ "Holden, MO" for a place GEOID, or None if it is not one.

    THE NAME COMES FROM THE CODE, NOT FROM WHOEVER SUPPLIED THE CODE.

    `article_enrichment.point_place` is the model's own words for where a
    story is centred, and beside it sits `point_geoid`, which the FIPS
    ladder resolved. When the model answers with a VENUE rather than a
    locality the geoid is still right and the string is not:

        2932572  Holden (11 articles)  /  "high school football
                 field/track" (1)
        2959096  Poplar Bluff (49)     /  "Three Rivers College" (1)
        2951644  Nevada (64)           /  "Ella Maxwell Fine Arts
                 Center" (1)
        2919792  Doniphan (38)         /  "Pilgrim's Rest Church" (1),
                 "near Highway C" (1)

    Eight such rows in production on 2026-09-15, across 832 geoids that
    carry a name. The harm is not the label: a visual that GROUPS by the
    string splits one place into two rows, so Holden showed 11 stories
    and its football field showed 1, and Holden was undercounted.

    Labelling from the geoid fixes every one of them and any future one,
    without the model having to improve.
    """
    _load()
    return _labels.get(str(geoid or "").strip())
