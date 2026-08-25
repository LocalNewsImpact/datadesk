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

# How accurately a quantity can be read off the page, from Cleveland and
# McGill's ranking of elementary perceptual tasks (1984) -- the experimental
# basis for the advice Cairo and every other teacher of this gives. Position
# on a common scale is read most accurately; shading and saturation least.
# Their headline result: readers judge a bar chart more accurately than a pie
# of the same numbers.
#
# It is a lower number for a better reading, so two types that can draw the
# same fields can be ordered by it. That is the difference between a picker
# that says what is possible and one that says what is better -- and the
# reason to keep the worse option available rather than hiding it: a donut
# is sometimes the right call, and being told the trade-off is not the same
# as being refused.
POSITION = 1  # bar, line, scatter -- a common axis
POSITION_APART = 2  # small multiples, non-aligned scales
LENGTH = 3  # a stacked segment, a chord ribbon
ANGLE = 5  # donut, pie
AREA = 6  # a sized dot
SHADING = 8  # a choropleth
NOT_QUANTITATIVE = 99  # a table states the number rather than drawing it


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
    #: How its main quantity is read. See the ranking above.
    encoding: int = POSITION
    #: Other questions this answers. The Data Viz Catalogue organises by
    #: function rather than by one family, and a chart serves several: a
    #: stacked bar is a comparison and a part-to-whole at once. `family` is
    #: where the gallery files it; these are the other ways to find it.
    functions: tuple = ()
    #: The row counts it reads well at, from the Berkeley chart picker's
    #: small/large split and the volume advice in their library guide: too
    #: many lines is unreadable, a scatter overplots, similarly-sized pie
    #: wedges mean pick something else. None either side means no limit.
    rows_from: int = 1
    rows_to: int = 0  # 0 = no upper limit
    #: Whether the value axis must start at zero. A bar's length *is* the
    #: quantity, so a truncated axis misstates it -- most of what Cairo's
    #: How Charts Lie is about. A line shows change, and forcing zero can
    #: flatten the thing being shown.
    zero_baseline: bool = False


# The Financial Times' Visual Vocabulary, which is the standard grouping for
# this audience: nine categories by *what is being said*, not by the shape of
# the data. Journalists already read it, so the gallery should not invent a
# tenth vocabulary for them to learn. Deviation, Distribution and Magnitude
# have no type yet and are declared anyway -- the gaps are the argument for
# what to build next, and a family with nothing in it is a visible gap
# rather than a silent one.
DEVIATION = "Deviation"
CORRELATION = "Correlation"
RANKING = "Ranking"
DISTRIBUTION = "Distribution"
CHANGE_OVER_TIME = "Change over time"
MAGNITUDE = "Magnitude"
PART_TO_WHOLE = "Part-to-whole"
SPATIAL = "Spatial"
FLOW = "Flow"
TABLES = "Tables"

FAMILIES = (
    DEVIATION,
    CORRELATION,
    RANKING,
    DISTRIBUTION,
    CHANGE_OVER_TIME,
    MAGNITUDE,
    PART_TO_WHOLE,
    SPATIAL,
    FLOW,
    TABLES,
)

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
        encoding=POSITION,
        functions=("Comparisons", "Ranking"),
        rows_to=30,
        zero_baseline=True,
    ),
    ChartType(
        "line",
        "Line chart",
        CHANGE_OVER_TIME,
        "Follow a value over time.",
        roles=(Role("x", "Time", accepts=(DATE, TEXT)), _VALUE, _SERIES),
        options=(_SORT, _TAXONOMY, *_AXIS_LABELS),
        also=("trend", "time series"),
        encoding=POSITION,
        functions=("Data over time", "Patterns"),
        rows_from=3,
        zero_baseline=False,
    ),
    ChartType(
        "area",
        "Area chart",
        CHANGE_OVER_TIME,
        "Follow a value over time, filled to the baseline.",
        roles=(Role("x", "Time", accepts=(DATE, TEXT)), _VALUE, _SERIES),
        options=(_SORT, _TAXONOMY, *_AXIS_LABELS),
        encoding=POSITION,
        functions=("Data over time", "Part-to-a-whole"),
        rows_from=3,
        zero_baseline=True,
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
        encoding=POSITION,
        functions=("Relationships", "Distribution"),
        rows_from=8,
        rows_to=5000,
    ),
    ChartType(
        "donut",
        "Donut chart",
        PART_TO_WHOLE,
        "Shares of a single total.",
        roles=(_CATEGORY, _VALUE),
        options=(Option("ylabel", "Label the value", "text"),),
        also=("pie",),
        encoding=ANGLE,
        functions=("Proportions", "Part-to-a-whole"),
        rows_to=6,
        zero_baseline=True,
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
        encoding=LENGTH,
        functions=("Relationships", "Movement or flow"),
        rows_to=400,
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
        encoding=LENGTH,
        functions=("Relationships", "Movement or flow"),
        rows_to=400,
    ),
    ChartType(
        "choropleth",
        "Shaded map",
        SPATIAL,
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
        encoding=SHADING,
        functions=("Location", "Comparisons"),
    ),
    ChartType(
        "points",
        "Dot map",
        SPATIAL,
        "A dot per place, sized by a count.",
        # A place, not a pair of numbers. Latitude and longitude as two
        # separate measures could never be filled from the corpus: a
        # pivot emits one measure per query, so the second slot always
        # named a column the rows did not have and a dot map could not be
        # built at all.
        #
        # Every county and place GEOID has a centroid in the Census
        # gazetteer, so a row grouped by a place carries its own
        # coordinates -- see `corpus.LAT_LABEL`. Choosing where the dots
        # are is choosing which places they are.
        roles=(
            Role("place", "Places", accepts=(GEO,)),
            Role("size", "Dot size", accepts=(NUMBER,), needs=False),
            Role("label", "Label", accepts=(TEXT,), needs=False),
        ),
        options=(
            Option("geo_level", "Geography", "choice"),
            Option("geo_fit", "Zoom to the data", "toggle"),
        ),
        also=("bubble map", "point map"),
        encoding=AREA,
        functions=("Location", "Distribution"),
        rows_to=5000,
    ),
    ChartType(
        "storymap",
        "Story map",
        SPATIAL,
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
        encoding=SHADING,
        functions=("Location", "Distribution"),
    ),
    ChartType(
        "table",
        "Table",
        TABLES,
        "The rows themselves.",
        encoding=NOT_QUANTITATIVE,
        functions=("Reference tool",),
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


# --- why a type is unavailable -----------------------------------------------
#
# Tableau's Show Me greys out the views that cannot be built from the fields
# currently chosen, and hovering one says what it would need -- "1 or more
# dimensions and 1 or more measures". Greying alone is a dead end; greying
# plus the requirement is a next step.
#
# We can say more than Tableau can, because the corpus declares what its
# dimensions mean. Tableau knows a field is a string; we know it is a county.

#: How each accepted type reads in a sentence.
_PLAIN = {
    NUMBER: "a number",
    TEXT: "a category",
    DATE: "a date",
    GEO: "a geography",
}


def _phrase(role):
    """ "a category" / "a number or a date", for one role."""
    parts = [_PLAIN.get(a, a) for a in role.accepts]
    if len(parts) == 1:
        return parts[0]
    return " or ".join([", ".join(parts[:-1]), parts[-1]])


def requirement_of(chart_id):
    """What a type needs, in a sentence. Shown on a greyed entry."""
    chart = BY_ID[chart_id]
    required = [r for r in chart.roles if r.needs]
    if not required:
        return "Needs nothing in particular."
    wants = ", ".join(f"{_phrase(r)} for {r.label.lower()}" for r in required)
    sentence = f"Needs {wants}."
    if chart.pairs:
        first, second = chart.pairs
        sentence += (
            f" {first.title()} and {second} must come from the same set of "
            "values — it compares a vocabulary with itself."
        )
    return sentence


def unavailable(chart_id, available):
    """Why this type cannot be built from `available`, or "" if it can.

    `available` is a mapping of field name to its type, which is what the
    pivot's output offers. The returned sentence is the hover text on a
    greyed gallery entry: what is missing, not merely that something is.

    Nothing is greyed when there is no data. A visual that has not run its
    query yet has no fields, and every type would fail -- a picker telling
    somebody to choose and then refusing all eleven is worse than one that
    checks nothing. The fields a type needs are what the later steps ask
    for; that is the answer, not a wall of refusals.
    """
    chart = BY_ID[chart_id]
    if not available:
        return ""
    kinds = list((available or {}).values())
    for role in chart.roles:
        if not role.needs:
            continue
        if not any(k in role.accepts for k in kinds):
            return (
                f"No field here is {_phrase(role)}, which {role.label.lower()} needs."
            )
    # A role cannot be filled twice, so two roles wanting the same single
    # field is a shortage rather than a match.
    for accepts in {r.accepts for r in chart.roles if r.needs}:
        wanted = sum(1 for r in chart.roles if r.needs and r.accepts == accepts)
        have = sum(1 for k in kinds if k in accepts)
        if have < wanted:
            plain = _PLAIN.get(accepts[0], accepts[0])
            verb = "is" if have == 1 else "are"
            return f"Needs {wanted} fields that are {plain}; there {verb} {have}."
    return ""


def read_more_accurately_than(chart_id, available):
    """Types that draw the same fields and are read more accurately.

    The suggestion half of the picker. Greying says what is impossible;
    this says what is available and better, which is the advice every
    teacher of this gives and no builder acts on. Cleveland and McGill
    measured it: readers judge a bar more accurately than a pie of the
    same numbers.

    A suggestion, never a substitution. A donut is sometimes the right
    call, and being told the trade-off is not the same as being refused.
    """
    here = BY_ID[chart_id]
    if here.encoding == NOT_QUANTITATIVE:
        return ()
    # Not within a family: the canonical advice crosses one. A pie is
    # Part-to-whole and a bar is Ranking, and the whole point is that the
    # bar reads better for the same numbers.
    #
    # But a map and a flow diagram are not chosen for their accuracy at
    # reading a quantity -- they are chosen because the question is where,
    # or between what. Offering a bar instead answers a different question
    # more precisely, which is not an improvement.
    if here.family in (SPATIAL, FLOW):
        return ()
    needed = tuple(sorted(r.accepts for r in here.roles if r.needs))
    # Labels, not ids: this is read by somebody choosing, and "bar" is what
    # the code calls it rather than what the gallery does.
    return tuple(
        c.label
        for c in CHART_TYPES
        if c.id != chart_id
        and c.encoding < here.encoding
        and c.family not in (SPATIAL, FLOW)
        and tuple(sorted(r.accepts for r in c.roles if r.needs)) == needed
        and not unavailable(c.id, available)
    )


def strains_at(chart_id, row_count):
    """A caution about volume, or "".

    Distinct from `unavailable`, and the distinction matters: this type can
    be built, and will read badly. The Berkeley chart picker splits small
    from large data sets as a first question; their library guide says why
    -- too many lines is unreadable, a scatter overplots, and a pie whose
    wedges are all much the same is a pie that should have been a bar.

    A caution and not a refusal. The author can see the count and decide.
    """
    chart = BY_ID[chart_id]
    if not row_count:
        return ""
    name = chart.label.lower()
    article = "an" if name[0] in "aeiou" else "a"
    if row_count < chart.rows_from:
        return (
            f"{row_count} rows is thin for {article} {name}; "
            f"it reads from about {chart.rows_from}."
        )
    if chart.rows_to and row_count > chart.rows_to:
        return (
            f"{row_count} rows is a lot for {article} {name}; "
            f"past about {chart.rows_to} it stops being readable."
        )
    return ""


#: Config keys the colour step sets for every chart type, so no type
#: declares them and every renderer may read them. `theme` is resolved
#: before a renderer runs and arrives as an argument; `taxonomy` is read
#: from the config directly, because whether a fixed vocabulary applies is
#: a question only the renderer can answer for its own marks.
SHARED_OPTIONS = ("theme", "taxonomy")


def gallery(available, row_count=0):
    """Every type, with the reason it cannot be used where that applies.

    The whole picker in one call: what is offered, grouped by the question
    it answers, and for each greyed entry the thing that would ungrey it.
    """
    return tuple(
        {
            "id": c.id,
            "label": c.label,
            "family": c.family,
            "blurb": c.blurb,
            "also": c.also,
            "requires": requirement_of(c.id),
            "why_not": unavailable(c.id, available),
            "read_better": read_more_accurately_than(c.id, available),
            "caution": strains_at(c.id, row_count),
            "zero_baseline": c.zero_baseline,
            "functions": c.functions,
        }
        for c in CHART_TYPES
    )


# --- what the data on hand actually holds ------------------------------------


def column_types(rows, sample=200):
    """{column: NUMBER|TEXT|DATE|GEO} from the rows themselves.

    Inferred rather than declared, because a visual's data can come from a
    pivot, a bucket object, a BigQuery result or an uploaded CSV, and only
    the first of those has a schema we control.

    A column is a number only if every non-empty value in the sample is
    one: a single "n/a" makes it text, because a chart that silently drops
    the rows it cannot parse is worse than one that is not offered.
    """
    import datetime
    import re

    geoid = re.compile(r"^\d{2}$|^\d{5}$|^\d{7}$|^\d{11}$|^\d{15}$")
    found = {}
    for name in (rows[0].keys() if rows else ()):
        values = [r.get(name) for r in rows[:sample] if r.get(name) not in (None, "")]
        if not values:
            found[name] = TEXT
            continue
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            found[name] = NUMBER
        elif all(isinstance(v, (datetime.date, datetime.datetime)) for v in values):
            found[name] = DATE
        elif all(isinstance(v, str) and geoid.match(v.strip()) for v in values):
            # Census codes arrive as strings and are identifiers, not
            # quantities: a chart must not average a county's FIPS.
            found[name] = GEO
        else:
            found[name] = TEXT
    return found
