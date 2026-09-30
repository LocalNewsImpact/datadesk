"""A layered map: newsrooms and coverage as the base, Census measures over it.

`run_layer_map` builds the payload for the `layermap` kind
(docs/LAYERED_MAP.md): the outlet map's points, the story map's coverage
shading, and up to `MAX_LAYERS` Census layers from `census_layer_value`,
each with its own scale. The reader switches between the fill layers in
the page; the points stay.

The scale of each layer is cut here, not in the browser. A story map cuts
its bands over the counties it paints, which the browser knows and the
server does not; a Census layer holds one state whole, so the server
knows exactly what is painted and can cut the deciles once, with the
precision the measure needs -- a percent to a tenth, a dollar to the
dollar -- rather than the story map's whole-story counts.
"""

from __future__ import annotations

import statistics

from datasets.geo import county_label
from visuals import census

KIND = "layermap"
#: How many fill layers one map may carry (decided 2026-09-30).
MAX_LAYERS = 8
#: Layers made from the map's own coverage and a Census denominator, by
#: county only, because stories are coded to counties. Newsrooms per
#: 100,000 residents was tested the same day and dropped: one paper in a
#: county of 1,934 people scored 51.7 and eight in St. Louis County 0.8,
#: which measures how small the county is, not how served it is.
DERIVED = {
    "stories_per_10k": {
        "key": "stories_per_10k",
        "label": "Stories per 10,000 residents",
        "group": "Coverage per resident",
        "kind": "value",
        "tract": False,
        "denominator": "total_population",
        "per": 10_000,
        "note": (
            "Stories from the newsrooms we collect, so a county served only "
            "by a print or uncollected paper reads low."
        ),
    }
}
LEVELS = census.LEVELS
#: Measures shown as money; the rest are counts, medians of years or
#: minutes, or percents.
DOLLARS = {
    "median_household_income",
    "per_capita_income",
    "median_home_value",
    "median_gross_rent",
}


def layers_of(config):
    """The layers a config asks for: [{variable, as}], capped and known."""
    out = []
    for layer in config.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        key = layer.get("variable")
        known = key in census.BY_KEY or key in DERIVED
        if not known or any(o["variable"] == key for o in out):
            continue
        out.append({"variable": key, "as": layer.get("as") or ""})
    return out[:MAX_LAYERS]


def level_of(config):
    level = config.get("layer_level") or "county"
    return level if level in LEVELS else "county"


def shows(config, base):
    """Whether a base layer is drawn: on unless the author turned it off."""
    return config.get(f"base_{base}", True) is not False


def format_of(variable, as_percent):
    if as_percent:
        return "percent"
    if variable["key"] in DOLLARS:
        return "dollars"
    return "number"


def cuts_for(values, steps=10):
    """Rising, de-duplicated quantile cuts over the values a layer holds.

    Ten by default, like a story map's deciles; fewer where the values tie.
    """
    known = sorted(v for v in values if v is not None)
    # One value everywhere is one band; a cut at it would draw an empty
    # band above.
    if len(known) < 2 or known[0] == known[-1]:
        return []
    steps = max(3, min(12, steps))
    out = []
    for i in range(1, steps):
        cut = statistics.quantiles(known, n=steps, method="inclusive")[i - 1]
        if not out or cut > out[-1]:
            out.append(cut)
    return [_tidy(c) for c in out if c < known[-1]]


def _tidy(value):
    """A cut with no more precision than a reader can use."""
    if abs(value) >= 1000:
        return float(round(value))
    return round(float(value), 1)


def _steps(config):
    bands = config.get("bands")
    if bands == "fixed":
        return 4
    try:
        return int(bands)
    except (TypeError, ValueError):
        return 10


def place_name(geoid, level):
    """ "Boone, MO" for a county; "Tract 9501, Adair, MO" for a tract."""
    county = county_label(geoid[:5])
    if level == "county":
        return county
    code = geoid[5:]
    tract = code[:4].lstrip("0") or "0"
    if code[4:] != "00":
        tract += "." + code[4:]
    return f"Tract {tract}, {county}"


def census_layer(layer, level, state, config, year=census.YEAR):
    """One Census fill layer: its areas, its scale, and what to call it."""
    from visuals.models import CensusValue

    variable = census.BY_KEY[layer["variable"]]
    as_percent = variable["kind"] == "share" and layer.get("as") != "value"
    rows = CensusValue.objects.filter(
        year=year, level=level, variable=variable["key"], geoid__startswith=state
    ).values_list("geoid", "estimate", "moe", "percent", "percent_moe")
    areas = []
    for geoid, estimate, moe, percent, percent_moe in rows:
        value, margin = (percent, percent_moe) if as_percent else (estimate, moe)
        areas.append(
            {
                "geoid": geoid,
                "name": place_name(geoid, level),
                "value": value,
                "moe": margin,
                "unreliable": census.unreliable(value, margin),
            }
        )
    areas.sort(key=lambda a: a["geoid"])
    return {
        "id": variable["key"],
        "label": variable["label"],
        "group": variable["group"],
        "format": format_of(variable, as_percent),
        "cuts": cuts_for([a["value"] for a in areas], _steps(config)),
        "unreliable": sum(1 for a in areas if a["unreliable"]),
        "areas": areas,
    }


def derived_layer(layer, coverage, level, state, config, year=census.YEAR):
    """A coverage-per-resident layer: the map's stories over a Census count.

    Every county of the state is a cell, a county with no stories at 0 --
    a count of none, not an absence. The margin is the denominator's, so
    small: these are not survey shares, and nothing is hatched.
    """
    from visuals.models import CensusValue

    spec = DERIVED[layer["variable"]]
    stories = {a["geoid"]: a.get("stories") or 0 for a in coverage}
    rows = CensusValue.objects.filter(
        year=year, level=level, variable=spec["denominator"], geoid__startswith=state
    ).values_list("geoid", "estimate")
    areas = []
    for geoid, people in rows:
        count = stories.get(geoid, 0)
        value = round(count / people * spec["per"], 1) if people else None
        areas.append(
            {
                "geoid": geoid,
                "name": place_name(geoid, level),
                "value": value,
                "moe": None,
                "unreliable": False,
                "note": f"{count:,} stories; {int(people):,} residents",
            }
        )
    areas.sort(key=lambda a: a["geoid"])
    return {
        "id": spec["key"],
        "label": spec["label"],
        "group": spec["group"],
        "format": "number",
        "cuts": cuts_for([a["value"] for a in areas], _steps(config)),
        "unreliable": 0,
        "note": spec["note"],
        "areas": areas,
    }


def _state_of(config):
    """The state the layers are read for: the frame's, else the focus's,
    else Missouri, which is what the store holds."""
    frame = config.get("frame") or []
    if frame:
        return str(frame[0])[:2]
    focus = str(config.get("focus") or "")
    return focus[:2] if len(focus) >= 2 and focus[:2].isdigit() else "29"


def run_layer_map(spec, scopes, config=None):
    """{points, areas, layers, meta} for a layered map."""
    from visuals.corpus import answer_once, run_story_map, scope_key
    from visuals.outlets import run_outlet_map

    config = config or {}
    level = level_of(config)
    state = _state_of(config)
    points, categories = [], []
    if shows(config, "points"):
        drawn = run_outlet_map(config)
        points = drawn["points"]
        categories = drawn["meta"].get("categories") or []
    wanted = layers_of(config)
    derived = [w for w in wanted if w["variable"] in DERIVED]
    coverage = []
    coverage_note = ""
    # The coverage is read when it is drawn, and when a derived layer
    # needs it; by county only, because stories are coded to counties.
    if level == "county" and (shows(config, "coverage") or derived):
        # Through the story map's own key, not beside it: this is the
        # story map's answer for the same slice, and the layered map's
        # key changes with every layer chosen while the coverage does not.
        # Read directly, every layer edit re-ran the points query, the
        # manual merge and the roll-up to keep only `areas`.
        coverage = answer_once(
            "visuals.storymap",
            [spec, scope_key(scopes), config.get("roll_up", "")],
            lambda: run_story_map(spec, scopes, config),
        )["areas"]
    elif level != "county" and (shows(config, "coverage") or derived):
        coverage_note = (
            "coverage shading and coverage per resident are drawn by county only"
        )
    layers = []
    for layer in wanted:
        if layer["variable"] in DERIVED:
            if level == "county":
                layers.append(derived_layer(layer, coverage, level, state, config))
        else:
            layers.append(census_layer(layer, level, state, config))
    if not shows(config, "coverage"):
        coverage = []
    meta = {
        "unit": "stories",
        "level": level,
        "categories": categories,
        "coverage": bool(coverage),
        "layers": len(layers),
        "year": census.YEAR,
        "points": len(points),
    }
    if coverage_note:
        meta["coverage_note"] = coverage_note
    if not points and not coverage and not layers:
        meta["empty_because"] = (
            "Nothing to draw: no layers chosen and both base layers off."
        )
    return {"points": points, "areas": coverage, "layers": layers, "meta": meta}
