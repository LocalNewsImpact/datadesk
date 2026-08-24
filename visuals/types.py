"""What each chart type needs, declared once.

The builder offers 25 chart-config controls on one page. No type reads more
than eleven of them and the median is three: a donut reads three, a story
map reads one, a table reads none. Every author sees all 25 and has to work
out which apply, which is most of why the page is unusable (ROADMAP item
20).

The fix is not fewer controls. It is that a control belongs to a chart type
rather than to the page, so the panel starts empty and grows as choices are
made. That means each type declares two things:

  roles    the data it needs -- what an author picks in the last step, as a
           checkbox list filtered to the dimensions whose type fits
  options  how it may be drawn -- shown only when the role each depends on
           has been filled

The shape follows RAWGraphs' chart interface, which solves the same problem:
`accepts` is its `validTypes`, `needs` its `required`, and `when` its
`requiredDimensions`.

This module is a description, not a renderer. `static/js/datadesk-chart.js`
still draws; `tests/test_chart_types.py` holds the two to each other, so a
control added to one and not the other is a failing test rather than a
setting that silently does nothing.
"""

from dataclasses import dataclass, field

#: What a dimension holds, for deciding which fields fit a role.
NUMBER, TEXT, DATE, GEO = "number", "text", "date", "geo"


@dataclass(frozen=True)
class Role:
    """A slot in a chart that a data field fills."""

    id: str
    label: str
    accepts: tuple = (NUMBER, TEXT, DATE)
    needs: bool = True  # the chart cannot draw without it
    many: bool = False


@dataclass(frozen=True)
class Option:
    """A setting, and the role whose presence brings it into view."""

    id: str
    label: str
    kind: str = "choice"  # choice | toggle | text
    when: str = ""  # a role id, or "" for always
    note: str = ""


@dataclass(frozen=True)
class ChartType:
    """A chart type, and what it needs to be drawn.

    `pairs` names two roles that must draw from the same vocabulary. A
    chord compares a set with itself -- primary and alternate CIN are the
    same ten needs -- and the renderer folds both columns into one list of
    names. Pairing publishers against needs is a heatmap, not a chord, and
    the picker should say so rather than drawing a diagram whose two halves
    mean different things.
    """

    id: str
    label: str
    #: The question it answers, which is how the gallery groups. Superset's
    #: SIP-67: somebody arrives with a question, not with a column count.
    family: str
    blurb: str
    roles: tuple = ()
    options: tuple = ()
    #: Names people search for that are not the label.
    also: tuple = field(default_factory=tuple)
    #: Roles that must come from one vocabulary. See the note above.
    pairs: tuple = ()


# Families, in the order the gallery shows them.
RANKING = "Ranking"
EVOLUTION = "Evolution"
CORRELATION = "Correlation"
PART_OF_WHOLE = "Part of a whole"
FLOW = "Flow"
MAPS = "Maps"
TABLES = "Tables"

FAMILIES = (RANKING, EVOLUTION, CORRELATION, PART_OF_WHOLE, FLOW, MAPS, TABLES)

# Shared by everything the generic renderer draws.
_CATEGORY = Role("x", "Category", accepts=(TEXT, DATE))
_VALUE = Role("y", "Value", accepts=(NUMBER,))
_SERIES = Role("series", "Split by", accepts=(TEXT,), needs=False)
_AXIS_LABELS = (
    Option("xlabel", "Label the horizontal axis", "text"),
    Option("ylabel", "Label the vertical axis", "text"),
)
_SORT = Option("sort", "Order by", "choice")
# Colour by a fixed taxonomy rather than by first appearance -- only
# meaningful once something is split into series.
_TAXONOMY = Option("taxonomy", "Category colours", "choice", when="series")


CHART_TYPES = (
    ChartType(
        "bar",
        "Bar chart",
        RANKING,
        "Compare a value across categories.",
        roles=(_CATEGORY, _VALUE, _SERIES),
        options=(
            _SORT,
            _TAXONOMY,
            Option("horizontal", "Lay the bars sideways", "toggle"),
            Option(
                "stacked",
                "Stack the series",
                "toggle",
                when="series",
                note="Off puts them side by side.",
            ),
            Option(
                "stack",
                "Stack as shares",
                "choice",
                when="series",
                note="Each column fills the axis and the series read as percentages.",
            ),
            *_AXIS_LABELS,
        ),
        also=("column", "ranking"),
    ),
    ChartType(
        "line",
        "Line chart",
        EVOLUTION,
        "Follow a value over time.",
        roles=(Role("x", "Time", accepts=(DATE, TEXT)), _VALUE, _SERIES),
        options=(_SORT, _TAXONOMY, *_AXIS_LABELS),
        also=("trend", "time series"),
    ),
    ChartType(
        "area",
        "Area chart",
        EVOLUTION,
        "Follow a value over time, filled to the baseline.",
        roles=(Role("x", "Time", accepts=(DATE, TEXT)), _VALUE, _SERIES),
        options=(_SORT, _TAXONOMY, *_AXIS_LABELS),
    ),
    ChartType(
        "scatter",
        "Scatter plot",
        CORRELATION,
        "Two measures against each other, one dot per row.",
        roles=(
            Role("x", "Horizontal", accepts=(NUMBER,)),
            Role("y", "Vertical", accepts=(NUMBER,)),
            _SERIES,
            Role("size", "Dot size", accepts=(NUMBER,), needs=False),
        ),
        options=(_SORT, _TAXONOMY, *_AXIS_LABELS),
    ),
    ChartType(
        "donut",
        "Donut chart",
        PART_OF_WHOLE,
        "Shares of a single total.",
        roles=(_CATEGORY, _VALUE),
        options=(Option("ylabel", "Label the value", "text"),),
        also=("pie",),
    ),
    ChartType(
        "chord",
        "Chord diagram",
        FLOW,
        "How much moves between every pair.",
        roles=(
            Role("from", "From", accepts=(TEXT,)),
            Role("to", "To", accepts=(TEXT,)),
            Role("value", "Amount", accepts=(NUMBER,)),
        ),
        pairs=("from", "to"),
        also=("network", "relationship"),
    ),
    ChartType(
        "arc",
        "Arc diagram",
        FLOW,
        "Connections along a single line.",
        roles=(
            Role("from", "From", accepts=(TEXT,)),
            Role("to", "To", accepts=(TEXT,)),
            Role("value", "Amount", accepts=(NUMBER,), needs=False),
        ),
        pairs=("from", "to"),
    ),
    ChartType(
        "choropleth",
        "Shaded map",
        MAPS,
        "Areas shaded by a value.",
        roles=(
            Role("geo_join", "Area code", accepts=(GEO, TEXT)),
            Role("geo_value", "Value", accepts=(NUMBER,)),
        ),
        options=(
            Option("geo_level", "Geography", "choice"),
            Option("geo_palette", "Palette", "choice", when="geo_value"),
            Option("geo_fit", "Zoom to the data", "toggle"),
        ),
        also=("heat map", "shaded", "county map"),
    ),
    ChartType(
        "points",
        "Dot map",
        MAPS,
        "A dot per row, placed by coordinates.",
        roles=(
            Role("lat", "Latitude", accepts=(NUMBER,)),
            Role("lon", "Longitude", accepts=(NUMBER,)),
            Role("size", "Dot size", accepts=(NUMBER,), needs=False),
            Role("label", "Label", accepts=(TEXT,), needs=False),
        ),
        options=(
            Option("geo_level", "Geography", "choice"),
            Option("geo_fit", "Zoom to the data", "toggle"),
        ),
        also=("bubble map", "point map"),
    ),
    ChartType(
        "storymap",
        "Story map",
        MAPS,
        "Where stories are set, and which counties they cover.",
        roles=(),  # the pivot's story_map shape supplies both layers whole
        options=(
            Option("focus", "Centre on", "text"),
            Option("focus_level", "Which is a", "choice"),
            Option("extent", "Show", "choice"),
            Option("extent_custom", "Also show", "text", when="extent"),
            Option(
                "bands",
                "Shading steps",
                "choice",
                note="Read by the renderer; no control offers it yet.",
            ),
        ),
        also=("coverage map",),
    ),
    ChartType(
        "table",
        "Table",
        TABLES,
        "The rows themselves.",
    ),
)

BY_ID = {c.id: c for c in CHART_TYPES}


def fits(role, dimension_type):
    """Whether a dimension may fill a role -- the checkbox-list filter."""
    return dimension_type in role.accepts


def options_for(chart_id, filled):
    """The options to show, given the roles filled so far.

    This is what keeps the panel empty at the start: an option whose role
    is unfilled is not a disabled control, it is not on screen.
    """
    chart = BY_ID[chart_id]
    have = set(filled or ())
    return tuple(o for o in chart.options if not o.when or o.when in have)


def can_draw(chart_id, filled):
    """Whether the preview has enough to render anything yet."""
    chart = BY_ID[chart_id]
    have = set(filled or ())
    return all(r.id in have for r in chart.roles if r.needs)
