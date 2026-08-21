"""City validation against the Census place gazetteer (SCOPE.md §2.4) —
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


def _norm(name):
    return re.sub(r"\s+", " ", name).strip().lower()


def _load():
    global _places
    if _places is not None:
        return _places
    places: dict[str, set[str]] = {}
    with open(_DATA, newline="") as fh:
        for row in csv.DictReader(fh):
            name = _norm(_SUFFIX.sub("", row["NAME"]))
            places.setdefault(row["USPS"], set()).add(name)
    _places = places
    return places


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
