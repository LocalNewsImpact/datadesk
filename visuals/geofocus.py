"""Where a map is centred, and how much of the country it paints.

Two questions the renderer used to answer with one field. `focus` named a
FIPS code and the framing was inferred from its length: a county focus
painted its whole state, because a lone county floating in white says
nothing about where it is. That is a reasonable default and a bad rule --
sometimes the county alone is the point, sometimes the answer is that
county and the four it borders.

So: `focus` is the place, at a named rung of the geography ladder, and
`extent` is how far out to paint. Both resolve here, against the same
gazetteer the corpus is coded with, so an author types "Boone, MO" rather
than looking up 29019.
"""

import csv
import re
from pathlib import Path

from datasets import geo
from datasets.geo import canonical_county, state_code, states_with_county
from datasets.places import place_geoid

#: The rungs an author can centre on. Named rather than inferred: "Boone"
#: is a county in eight states and a city in three, and guessing which one
#: somebody meant is how a map ends up over the wrong half of the country.
CITY, COUNTY, STATE = "city", "county", "state"
LEVELS = (CITY, COUNTY, STATE)

#: How much to paint around it.
#:   selected  only the focused county -- the city's county, or the county
#:   county    the county containing the focus (same as selected for a county)
#:   state     every county in the focus's state
#:   custom    the focus's county plus whatever else was named
#:   auto      the renderer decides from where the stories are
SELECTED, WHOLE_COUNTY, WHOLE_STATE, CUSTOM, AUTO = (
    "selected",
    "county",
    "state",
    "custom",
    "auto",
)
EXTENTS = (AUTO, SELECTED, WHOLE_COUNTY, WHOLE_STATE, CUSTOM)

_FIPS = re.compile(r"^\d{2}$|^\d{5}$|^\d{7}$")
_COUNTIES = None


class FocusError(ValueError):
    """A focus or extent that names no place. The message is user-facing."""


def _counties():
    """Every county FIPS, and the state FIPS each belongs to.

    Read from the same file `canonical_county` uses, so the two cannot
    disagree about what exists.
    """
    global _COUNTIES
    if _COUNTIES is None:
        path = Path(geo.__file__).resolve().parent / "data" / "census_counties.csv"
        rows = []
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                fips, usps = row.get("GEOID", "").strip(), row.get("USPS", "").strip()
                if len(fips) == 5:
                    rows.append((fips, usps.upper()))
        _COUNTIES = rows
    return _COUNTIES


def state_fips(usps):
    """Two-digit FIPS for a USPS code, from the county table.

    Every county GEOID begins with its state's, so the mapping is already
    in the file rather than in a second table that could drift from it.
    """
    code = (usps or "").strip().upper()
    for fips, state in _counties():
        if state == code:
            return fips[:2]
    return None


def counties_in_state(fips):
    """Every county FIPS in a state."""
    two = str(fips)[:2]
    return [c for c, _ in _counties() if c.startswith(two)]


def resolve(value, level=""):
    """(geoid, level) for a named place, or ("", "") if blank.

    `level` names the rung. Without one the rung is inferred, which is why
    naming it is better: a bare county name in eight states is refused
    rather than guessed at.
    """
    text = (value or "").strip()
    if not text:
        return "", ""

    if _FIPS.match(text):
        by_length = {2: STATE, 5: COUNTY, 7: CITY}
        return text, level or by_length[len(text)]

    name, _, state_part = (p.strip() for p in text.rpartition(","))
    if not name:  # no comma: the whole string is the name
        name, state_part = text, ""

    if level == STATE or (not level and not state_part and state_code(text)):
        code = state_code(text)
        if not code:
            raise FocusError(f"No state called {text!r}.")
        fips = state_fips(code)
        if not fips:
            raise FocusError(f"No boundary data for {text!r}.")
        return fips, STATE

    if level == CITY:
        if not state_part:
            raise FocusError(f"Name the state too — '{name}, MO'.")
        found = place_geoid(state_part, name)
        if not found:
            raise FocusError(f"No city called {name!r} in {state_part!r}.")
        return found, CITY

    # County, named or inferred.
    if state_part:
        fips, _ = canonical_county(state_part, name)
        if fips:
            return fips, COUNTY
        raise FocusError(f"No county called {name!r} in {state_part!r}.")

    states = states_with_county(name)
    if len(states) == 1:
        fips, _ = canonical_county(states[0], name)
        if fips:
            return fips, COUNTY
    if len(states) > 1:
        shown = ", ".join(sorted(states)[:8])
        more = "" if len(states) <= 8 else f" and {len(states) - 8} more"
        raise FocusError(
            f"{name} County is in {shown}{more}. Name the state too — "
            f"'{name}, {sorted(states)[0]}'."
        )
    raise FocusError(f"No state, county or city called {name!r}.")


def frame(geoid, level, extent=AUTO, custom=""):
    """The county FIPS a map should paint, or [] to let the renderer decide.

    A list, computed here, because the gazetteer is here: the renderer is
    given counties to draw rather than a rule to apply, so what is painted
    can be read off the saved config rather than re-derived.
    """
    if not geoid or extent == AUTO:
        return []

    own = geo.to_county(geoid, "place" if level == CITY else level)
    if level == STATE:
        own = geoid[:2]

    if extent == WHOLE_STATE:
        return sorted(counties_in_state(own or geoid))
    if extent in (SELECTED, WHOLE_COUNTY):
        if level == STATE:
            # A state has no single county; the honest reading of "only
            # what is selected" is the state itself.
            return sorted(counties_in_state(geoid))
        if not own:
            raise FocusError("That place does not sit in a county we can find.")
        return [own]
    if extent == CUSTOM:
        wanted = []
        if own:
            wanted.append(own)
        known = {c for c, _ in _counties()}
        # Semicolons and newlines, never commas: a comma belongs to the
        # place itself ("Callaway, MO"), and splitting on it turns one
        # county into a county and a state that is not one.
        for piece in re.split(r"[;\n]+", custom or ""):
            piece = piece.strip()
            if not piece:
                continue
            if piece in known:
                wanted.append(piece)
                continue
            found, found_level = resolve(piece)
            county = (
                found[:5]
                if found_level == COUNTY
                else geo.to_county(
                    found, "place" if found_level == CITY else found_level
                )
            )
            if not county:
                raise FocusError(f"{piece!r} is not a county.")
            wanted.append(county)
        return sorted(set(wanted))
    raise FocusError(f"Unknown extent: {extent!r}")
