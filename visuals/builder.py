"""The form-driven builder's server side (SCOPE.md §2.7 v2).

The builder produces a config dict the chart runtime reads; the server
whitelists its keys and validates the kind, and everything downstream —
snapshots, publishing, the pinned embed — is the v1 machinery unchanged.
"""

import csv
import io

CHART_KINDS = (
    "table",
    "bar",
    "line",
    "area",
    "scatter",
    "donut",
    "chord",
    "arc",
    "choropleth",
    "points",
    "storymap",
)

_STRING_KEYS = (
    "x",
    "y",
    "series",
    "size",
    "theme",
    "theme_mode",
    "taxonomy",
    "title",
    "from",
    "to",
    "value",
    "label",
    "sort",
    "stack",
    "xlabel",
    "ylabel",
    "subtitle",
    "source",
    "credit",
    "geo_level",
    "geo_join",
    "geo_value",
    "geo_palette",
    "focus",
    "bands",
    "lat",
    "lon",
)
_BOOL_KEYS = ("horizontal", "stacked", "geo_fit")
# "stack" is a string ("percent") rather than a flag.

MAX_ROWS = 20_000


class BuilderError(ValueError):
    """User-facing builder problem."""


def config_from_form(post, default_state=""):
    """Assemble and validate a chart config from form fields."""
    kind = post.get("kind", "")
    if kind not in CHART_KINDS:
        raise BuilderError(f"Unknown chart kind: {kind or '(none)'}")
    config = {"kind": kind}
    for key in _STRING_KEYS:
        value = post.get(key, "").strip()
        if value:
            config[key] = value
    for key in _BOOL_KEYS:
        if post.get(key) == "1":
            config[key] = True
    # The renderer frames on a FIPS code because that is what the boundary
    # file is keyed by. Nobody knows Boone County is 29019, so the
    # gazetteer resolves the name here rather than the author looking it
    # up. Codes pass through unchanged.
    if config.get("focus"):
        from visuals.geofocus import AUTO, FocusError, frame, resolve

        try:
            geoid, level = resolve(
                config["focus"], post.get("focus_level", ""), default_state
            )
            config["focus"] = geoid
            config["focus_level"] = level
            extent = post.get("extent", AUTO) or AUTO
            config["extent"] = extent
            counties = frame(
                geoid, level, extent, post.get("extent_custom", ""), default_state
            )
        except FocusError as exc:
            raise BuilderError(str(exc)) from exc
        # The counties to paint, resolved now. The renderer is handed a list
        # rather than a rule, so what a published map shows can be read off
        # its config instead of re-derived from a gazetteer that moves.
        if counties:
            config["frame"] = counties
        if custom := post.get("extent_custom", "").strip():
            config["extent_custom"] = custom
    return config


def parse_upload(uploaded_file):
    """Parse an uploaded CSV into typed rows for an inline snapshot.

    Columns whose every non-empty value parses as a number become
    numbers, so the runtime and BigQuery/GCS sources agree on types.
    """
    try:
        text = uploaded_file.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BuilderError("Not UTF-8. Save the file as UTF-8 CSV.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BuilderError("No header row found.")
    rows = list(reader)
    if not rows:
        raise BuilderError("No data rows found.")
    if len(rows) > MAX_ROWS:
        raise BuilderError(f"{len(rows)} rows; the limit is {MAX_ROWS}.")

    numeric = set(reader.fieldnames)
    for row in rows:
        for column in list(numeric):
            value = (row.get(column) or "").strip()
            if value:
                try:
                    float(value)
                except ValueError:
                    numeric.discard(column)
    typed = []
    for row in rows:
        out = {}
        for column in reader.fieldnames:
            value = (row.get(column) or "").strip()
            if column in numeric and value:
                number = float(value)
                out[column] = int(number) if number.is_integer() else number
            else:
                out[column] = value or None
        typed.append(out)
    return typed


# Which of the runtime's three libraries each kind actually uses, so an
# embed downloads what it draws with and nothing else.
#
# Plot reads globalThis.d3 in its UMD factory rather than bundling it, so
# d3 is a dependency of Plot and not an alternative to it. The kinds that
# draw their own SVG -- donut, chord, arc, storymap -- use d3 alone and
# were paying for Plot regardless; a table draws in plain DOM and was
# paying for all of it.
#
# Anything not named here loads everything. A kind added without a line in
# this table should render slowly, not fail to render.
CHART_LIBS = {
    "table": (),
    "donut": ("d3",),
    "chord": ("d3",),
    "arc": ("d3",),
    "storymap": ("d3", "topojson"),
    "bar": ("d3", "plot"),
    "line": ("d3", "plot"),
    "area": ("d3", "plot"),
    "scatter": ("d3", "plot"),
    "choropleth": ("d3", "plot", "topojson"),
    "points": ("d3", "plot", "topojson"),
}

#: Every library, for a kind this table does not know about.
ALL_LIBS = ("d3", "plot", "topojson")


def libs_for(kind):
    """The runtime libraries a kind needs, in load order."""
    wanted = set(CHART_LIBS.get(kind, ALL_LIBS))
    return tuple(lib for lib in ALL_LIBS if lib in wanted)
