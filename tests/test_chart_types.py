"""The declarations, held against the renderers that read them.

`visuals/types.py` says what each chart type needs. `datadesk-chart.js`
draws. Nothing makes the two agree, so these do: a control added to one and
not the other is a failing test rather than a setting that silently does
nothing, which is how the builder came to offer twenty-five chart-config
controls of which no type reads more than eleven.
"""

import re
from pathlib import Path

import pytest

from visuals.builder import CHART_KINDS
from visuals.types import (
    BY_ID,
    CHART_TYPES,
    FAMILIES,
    Role,
    can_draw,
    fits,
    options_for,
)

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static/js/datadesk-chart.js").read_text()


def _reads(fn):
    """The config keys a render function actually reads."""
    match = re.search(rf"function {fn}\(", JS)
    if not match:
        return set()
    start = match.start()
    following = re.search(r"\n  function ", JS[start + 10 :])
    body = JS[start : start + 10 + following.start()] if following else JS[start:]
    keys = set(re.findall(r"config\.([a-zA-Z_][a-zA-Z_0-9]*)", body))
    destructured = re.search(r"const \{([^}]*)\} = config", body)
    if destructured:
        keys |= {k.strip() for k in destructured.group(1).split(",") if k.strip()}
    return keys - {"kind"}


# --- the declarations cover what exists --------------------------------------


def test_every_chart_kind_is_declared():
    """A kind the builder accepts and nothing describes is a kind whose
    controls can only be found by reading the renderer."""
    assert set(BY_ID) == set(CHART_KINDS)


def test_every_type_names_a_family_the_gallery_shows():
    """The gallery groups by the question somebody arrives with, not by the
    data's shape (Superset's SIP-67)."""
    for chart in CHART_TYPES:
        assert chart.family in FAMILIES, chart.id


def test_every_type_says_what_it_is_for():
    for chart in CHART_TYPES:
        assert chart.blurb.strip(), chart.id


def test_role_and_option_ids_are_unique_within_a_type():
    for chart in CHART_TYPES:
        ids = [r.id for r in chart.roles]
        assert len(ids) == len(set(ids)), chart.id
        keys = [o.id for o in chart.options]
        assert len(keys) == len(set(keys)), chart.id


# --- the declarations match the renderers ------------------------------------


@pytest.mark.parametrize(
    "chart_id,fn",
    [
        ("chord", "renderChord"),
        ("arc", "renderArc"),
        ("donut", "renderDonut"),
    ],
)
def test_a_types_roles_are_what_its_renderer_reads(chart_id, fn):
    """These three have a renderer to themselves, so the comparison is
    exact. The four sharing the generic renderer are covered below."""
    from visuals.types import SHARED_OPTIONS

    declared = (
        {r.id for r in BY_ID[chart_id].roles}
        | {o.id for o in BY_ID[chart_id].options}
        # Set by the colour step for every type, so declared by none.
        | set(SHARED_OPTIONS)
    )
    assert _reads(fn) <= declared, f"{fn} reads something undeclared"


def test_the_story_map_declares_what_its_renderer_reads():
    """Including `bands`, which the renderer reads and no control offers --
    the reverse of a dead control, and just as invisible."""
    declared = {o.id for o in BY_ID["storymap"].options}
    assert _reads("renderStoryMap") <= declared | {"frame"}
    assert "bands" in declared


def test_bar_alone_declares_the_bar_only_options():
    """horizontal, stacked and stack sit inside `if (kind === "bar")`. A
    line chart offering them is the dead-control problem in miniature."""
    bar = {o.id for o in BY_ID["bar"].options}
    assert {"horizontal", "stacked", "stack"} <= bar
    for other in ("line", "area", "scatter"):
        assert not {"horizontal", "stacked", "stack"} & {
            o.id for o in BY_ID[other].options
        }, other


def test_only_scatter_declares_a_size_role_among_the_generic_four():
    """`config.size` is read inside the scatter branch."""
    assert "size" in {r.id for r in BY_ID["scatter"].roles}
    for other in ("bar", "line", "area"):
        assert "size" not in {r.id for r in BY_ID[other].roles}, other


# --- what the flow needs from them -------------------------------------------


def test_the_panel_starts_empty_and_grows():
    """An option whose role is unfilled is not a disabled control; it is
    not on screen. This is what keeps the page from opening as a wall."""
    before = options_for("bar", [])
    after = options_for("bar", ["x", "y", "series"])
    assert len(before) < len(after)
    assert "taxonomy" not in {o.id for o in before}
    assert "taxonomy" in {o.id for o in after}


def test_the_preview_knows_when_it_can_draw():
    assert not can_draw("bar", [])
    assert not can_draw("bar", ["x"])
    assert can_draw("bar", ["x", "y"])
    assert can_draw("bar", ["x", "y", "series"]), "an optional role is optional"


def test_a_field_fits_a_role_by_type():
    """This is the last step's checkbox list: the fields shown are the ones
    that fit, so nobody picks a date for a measure and finds out later."""
    from visuals.types import NUMBER, TEXT

    value = Role("y", "Value", accepts=(NUMBER,))
    assert fits(value, NUMBER)
    assert not fits(value, TEXT)


def test_a_chord_compares_a_vocabulary_with_itself():
    """Both ends are the same ten CIN needs, which is why the renderer
    folds both columns into one list of names. Publishers against needs is
    a heatmap, not a chord."""
    assert BY_ID["chord"].pairs == ("from", "to")
    assert BY_ID["arc"].pairs == ("from", "to")
    assert BY_ID["bar"].pairs == ()


def test_no_type_asks_for_more_than_the_form_offers_today():
    """The audit's finding, kept honest: the builder's twenty-five
    chart-config controls, and no type reading more than eleven."""
    form = (ROOT / "templates/visuals/builder_edit.html").read_text()
    offered = set(re.findall(r'name="([a-z_0-9]+)"', form))
    for chart in CHART_TYPES:
        wanted = {r.id for r in chart.roles} | {o.id for o in chart.options}
        missing = wanted - offered
        # `bands` is the known gap: read by the renderer, offered by nothing.
        assert missing <= {"bands"}, f"{chart.id} wants {missing}"


# --- what the picker tells somebody choosing ---------------------------------
#
# Tableau's Show Me greys the views that cannot be built and, on hover, says
# what they would need. Greying alone is a dead end; greying plus the
# requirement is a next step. These assert the second half.


def test_a_greyed_type_says_what_is_missing():
    from visuals.types import NUMBER, TEXT, unavailable

    only_text = {"County": TEXT}
    reason = unavailable("bar", only_text)
    assert "a number" in reason, reason

    both = {"County": TEXT, "Articles": NUMBER}
    assert unavailable("bar", both) == ""


def test_two_roles_of_one_kind_need_two_fields():
    """A role cannot be filled twice. One number does not make a scatter,
    and saying 'needs 2, there is 1' is the sentence that leads somewhere."""
    from visuals.types import NUMBER, TEXT, unavailable

    reason = unavailable("scatter", {"County": TEXT, "Articles": NUMBER})
    assert "2" in reason and "1" in reason, reason


def test_the_requirement_reads_as_a_sentence():
    from visuals.types import requirement_of

    assert requirement_of("bar").startswith("Needs ")
    # The pairing constraint is part of what a chord requires.
    assert "same set of values" in requirement_of("chord")


def test_a_donut_is_offered_but_a_bar_is_suggested():
    """Cleveland and McGill's headline result: a bar is read more
    accurately than a pie of the same numbers. Said where somebody is
    choosing, not in a style guide nobody opens."""
    from visuals.types import NUMBER, TEXT, read_more_accurately_than, unavailable

    fields = {"County": TEXT, "Articles": NUMBER}
    assert unavailable("donut", fields) == "", "a donut is still allowed"
    assert "Bar chart" in read_more_accurately_than("donut", fields)


def test_nothing_is_suggested_over_a_map_or_a_flow():
    """A map is chosen because the question is where, and a chord because
    the question is between what. A bar answers a different question more
    precisely, which is not an improvement."""
    from visuals.types import NUMBER, TEXT, read_more_accurately_than

    fields = {"County": TEXT, "Articles": NUMBER, "Other": TEXT}
    assert read_more_accurately_than("choropleth", fields) == ()
    assert read_more_accurately_than("chord", fields) == ()


def test_a_bar_has_nothing_suggested_over_it():
    """Position on a common scale is the top of the ranking."""
    from visuals.types import NUMBER, TEXT, read_more_accurately_than

    assert read_more_accurately_than("bar", {"County": TEXT, "Articles": NUMBER}) == ()


def test_the_families_are_the_visual_vocabularys():
    """The Financial Times' nine, which this audience already reads. Three
    carry nothing yet -- a visible gap rather than a silent one."""
    from collections import Counter

    from visuals.types import (
        DEVIATION,
        DISTRIBUTION,
        FAMILIES,
        MAGNITUDE,
    )

    assert len(FAMILIES) == 10  # the nine, plus tables
    have = Counter(c.family for c in CHART_TYPES)
    for empty in (DEVIATION, DISTRIBUTION, MAGNITUDE):
        assert empty in FAMILIES
        assert have.get(empty, 0) == 0, f"{empty} has a type now; update the note"


def test_the_gallery_answers_the_whole_picker_in_one_call():
    from visuals.types import NUMBER, TEXT, gallery

    entries = gallery({"County": TEXT, "Articles": NUMBER})
    assert len(entries) == len(CHART_TYPES)
    for entry in entries:
        assert set(entry) == {
            "id",
            "label",
            "family",
            "blurb",
            "also",
            "functions",
            "requires",
            "why_not",
            "read_better",
            "caution",
            "zero_baseline",
        }


# --- the picker screen -------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_picker_greys_what_the_data_cannot_build(client, crawler_schema):
    """The gallery is rendered against the visual's own snapshot, so what is
    dimmed reflects the data actually on hand rather than a general rule."""
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant
    from visuals.models import Visual, VisualSnapshot

    author = User.objects.create_user("designer", email="d@localnewsimpact.org")
    Grant.objects.create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)

    visual = Visual.objects.create(
        slug="picker-test",
        title="Picker",
        template="builder",
        source_kind="corpus",
        created_by=author,
    )
    VisualSnapshot.objects.create(
        visual=visual,
        version=1,
        data=[{"County": "Boone", "Articles": 12}],
        created_by=author,
    )

    body = client.get(f"/visuals/builder/{visual.slug}/type/").content.decode()

    # A bar can be drawn from a category and a number; a scatter cannot,
    # and says why rather than merely being dim.
    assert "Bar chart" in body
    assert "Scatter plot" in body
    assert "there is 1" in body, "the greyed reason names the shortage"
    # And the accuracy suggestion is on the donut.
    assert "Reads more accurately as" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_choosing_a_type_keeps_every_other_choice(client, crawler_schema):
    """Changing the type is the only step that can invalidate an earlier
    choice, and the rule is to keep it. A builder that empties the form
    when somebody looks at another chart type teaches them not to explore."""
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant
    from visuals.models import Visual

    author = User.objects.create_user("designer2", email="d2@localnewsimpact.org")
    Grant.objects.create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)

    visual = Visual.objects.create(
        slug="keeps-choices",
        title="Keeps",
        template="builder",
        source_kind="corpus",
        created_by=author,
        config={"kind": "bar", "x": "County", "y": "Articles", "title": "Mine"},
    )
    client.post(f"/visuals/builder/{visual.slug}/type/", {"kind": "donut"})

    visual.refresh_from_db()
    assert visual.config["kind"] == "donut"
    assert visual.config["x"] == "County", "the mapping survived"
    assert visual.config["title"] == "Mine"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_unknown_type_is_refused(client, crawler_schema):
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant
    from visuals.models import Visual

    author = User.objects.create_user("designer3", email="d3@localnewsimpact.org")
    Grant.objects.create(user=author, app=DATADESK, scope="", role="editor")
    client.force_login(author)
    visual = Visual.objects.create(
        slug="refuses",
        title="R",
        template="builder",
        source_kind="corpus",
        created_by=author,
    )
    assert (
        client.post(
            f"/visuals/builder/{visual.slug}/type/", {"kind": "sunburst"}
        ).status_code
        == 404
    )


def test_a_column_of_census_codes_is_not_a_quantity():
    """A county's FIPS is an identifier. A chart must not average it, and
    offering it as a measure is how that happens."""
    from visuals.types import GEO, NUMBER, column_types

    found = column_types([{"county": "29019", "stories": 81}])
    assert found["county"] == GEO
    assert found["stories"] == NUMBER


def test_one_unparseable_value_makes_a_column_text():
    """A chart that silently drops the rows it cannot parse is worse than
    one that is not offered."""
    from visuals.types import TEXT, column_types

    assert column_types([{"n": 1}, {"n": 2}, {"n": "n/a"}])["n"] == TEXT


# --- the builder's steps -----------------------------------------------------


def test_the_steps_are_the_order_the_prototype_settled():
    from visuals.steps import STEPS

    assert [s.slug for s in STEPS] == [
        "type",
        "theme",
        "data",
        "newsrooms",
        "fields",
        # Publishing is the end of the flow, not a corner of the advanced
        # settings page: it is where the work is finished and where the
        # embed code is handed over.
        "publish",
    ]


def test_no_two_steps_own_the_same_key():
    """Going back changes one choice and keeps the rest, which only holds
    if a step's keys are its own. Two steps writing one key means entering
    the second silently undoes the first."""
    from visuals.steps import STEPS

    seen = {}
    for step in STEPS:
        for key in step.owns:
            assert (
                ":" in key
            ), f"{step.slug} owns {key!r} without saying which store it is in"
            assert key not in seen, f"{key} owned by {seen.get(key)} and {step.slug}"
            seen[key] = step.slug


def test_a_step_counts_as_done_when_it_decided_something():
    """Not when somebody looked at it. Opening the colour panel and
    choosing nothing leaves a default nobody chose."""
    from visuals.models import Visual
    from visuals.steps import reached

    empty = Visual(config={}, spec={})
    assert reached(empty) == set()

    typed = Visual(config={"kind": "chord"}, spec={})
    assert reached(typed) == {"type"}

    most = Visual(
        config={"kind": "chord", "theme": "newsprint"},
        spec={"dataset": "mo", "dimensions": ["cin_primary"]},
    )
    assert reached(most) == {"type", "theme", "data", "fields"}


def test_the_last_step_leads_nowhere():
    from visuals.steps import next_after

    assert next_after("type") == "theme"
    assert next_after("fields") == "publish"
    assert next_after("publish") is None


# --- the theme swatches ------------------------------------------------------


def test_every_theme_the_panel_offers_exists_in_the_runtime():
    """A theme offered here and absent there saves a value the chart falls
    back from, so the author picks a palette and gets the default."""
    import re
    from pathlib import Path

    from visuals.panels import THEMES

    js = (
        Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"
    ).read_text()
    block = js[js.index("const THEMES = {") : js.index("  function theme(")]
    defined = set(re.findall(r"^    ([a-z]+): \{", block, re.M))
    for theme_id, _, _ in THEMES:
        assert theme_id in defined, f"{theme_id} is offered and not defined"


def test_a_swatch_shows_the_colours_the_chart_will_use():
    """Copied rather than read, because Python cannot run the JS. A palette
    changed there and not here shows the wrong swatch, which is worse than
    no swatch -- so the first colour of each is held to the runtime."""
    import re
    from pathlib import Path

    from visuals.panels import THEMES

    js = (
        Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"
    ).read_text()
    block = js[js.index("const THEMES = {") : js.index("  function theme(")]
    base = re.search(r"const LIGHT = \{\s*series: \[([^\]]*)\]", js).group(1)
    base = [c.strip().strip('"') for c in base.split(",") if c.strip()]

    for theme_id, _, colours in THEMES:
        segment = block[block.index(f"    {theme_id}: {{") :]
        light = segment[: segment.index("dark:")] if "dark:" in segment else segment
        found = re.search(r"series: \[([^\]]*)\]", light)
        actual = (
            [c.strip().strip('"') for c in found.group(1).split(",") if c.strip()]
            if found
            else base
        )
        assert list(colours) == actual[: len(colours)], (
            f"{theme_id}: the swatch shows {list(colours)} and the chart "
            f"draws {actual[: len(colours)]}"
        )


def test_the_swatch_keeps_a_focus_ring():
    """The radio is hidden so the palette can be the target, which takes
    the ring with it unless the label carries one."""
    from pathlib import Path

    css = (
        Path(__file__).resolve().parent.parent / "static/css/datadesk.css"
    ).read_text()
    assert ".swatch:focus-within" in css


def test_nothing_is_greyed_before_there_is_data():
    """A visual that has not run its query has no fields, so every type
    would fail its check. A picker that says "pick a type" and then refuses
    all eleven is worse than one that checks nothing -- what a type needs
    is what the later steps ask for."""
    from visuals.types import gallery, unavailable

    assert unavailable("scatter", {}) == ""
    assert unavailable("chord", {}) == ""
    assert all(not e["why_not"] for e in gallery({}))


def test_greying_returns_as_soon_as_there_is_data():
    from visuals.types import NUMBER, TEXT, unavailable

    assert unavailable("scatter", {"County": TEXT, "Articles": NUMBER})


# --- chord labels have to fit the ring they sit on ---------------------------


def _chart_js():
    from pathlib import Path

    return (
        Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"
    ).read_text()


def test_a_label_is_given_the_space_between_its_neighbours():
    """Its own arc was the first answer and it is wrong twice over. A name
    longer than its arc overflowed a path that stopped at the arc's ends,
    and SVG clips a textPath at both -- which is how "Environment and
    Planning" lost its E as well as its tail.

    And a real distribution is lopsided: Civic Life takes a quarter of the
    ring while Economic Development takes a few degrees, so sizing to the
    arc means the small categories can never be named at all.
    """
    js = _chart_js()
    body = js[js.index("const LABEL_R = R + BAND") :]
    body = body[: body.index("const ribbons")]
    assert "mids" in body and "spans" in body
    # Halfway to the neighbouring arc on each side, not the arc's own width.
    assert "Math.min(left, right)" in body
    assert "d.endAngle - d.startAngle" not in body, "sized to its own arc again"


def test_the_trim_measures_rather_than_estimating():
    """A character-width guess is what let text run past the end of its
    path, where SVG cuts it without a mark -- a reader cannot tell a
    truncated name from a short one."""
    js = _chart_js()
    assert "getComputedTextLength()" in js
    assert "const CHAR =" not in js, "back to guessing at character widths"


def test_the_trim_runs_with_the_chart_in_the_document():
    """getComputedTextLength on a detached node returns zero, and every
    label would 'fit'."""
    js = _chart_js()
    insert = js.index("el.replaceChildren(svg.node());\n    fitLabels(")
    assert insert > 0, "the fitting pass must follow the insertion"


def test_a_name_that_cannot_be_shortened_is_dropped_not_stubbed():
    """Better nothing than "E…". The tooltip and the table still carry
    it."""
    js = _chart_js()
    body = js[js.index("function fitLabels(") :]
    body = body[: body.index("\n  function ")]
    assert "text.remove()" in body
    assert "n > 3" in body


# --- the preview gets the width of its pane ----------------------------------


def _console_css():
    from pathlib import Path

    return (
        Path(__file__).resolve().parent.parent / "static/css/datadesk.css"
    ).read_text()


def test_the_preview_pane_does_not_size_itself_to_its_contents():
    """`place-items: center` centres by shrinking the item to its content.
    `render` empties the element before it measures, so the box collapsed to
    nothing and every chart -- not only the sankey -- drew at the fallback
    width in a pane twice that wide."""
    css = _console_css()
    rule = css[css.index(".build-stage .canvas{") :]
    rule = rule[: rule.index("}")]
    assert "place-items: center" not in rule, "the preview shrinks to fit again"
    assert "justify-items: stretch" in rule


def test_the_waiting_state_is_still_centred():
    """It was centred by the grid that has just stopped centring."""
    css = _console_css()
    rule = css[css.index(".canvas .empty{") :]
    assert "margin-inline: auto" in rule[: rule.index("}")]


def test_a_chart_that_measures_nothing_asks_its_container():
    """`el.clientWidth || 640` turned an unmeasurable element into a chart
    drawn at 640px, which looks like a chart rather than like a bug, and so
    went unreported until somebody said the preview was narrow."""
    js = _chart_js()
    assert "el.clientWidth || 640" not in js, "back to a silent fallback width"
    body = js[js.index("function roomFor(") :]
    body = body[: body.index("\n  function ")]
    # The climb has to discount padding, or a chart is drawn the width of the
    # box around it and overflows by exactly that padding.
    assert "parentElement" in body
    assert "paddingLeft" in body and "paddingRight" in body


def test_the_observer_measures_what_the_renderer_measures():
    """A pane that widens would redraw at a width the chart does not use."""
    js = _chart_js()
    body = js[js.index("function mount(") :]
    body = body[: body.index("return { redraw: draw };")]
    assert "roomFor(el)" in body
    assert "el.clientWidth" not in body, "the two measurements disagree again"


# --- every drawn kind answers the pointer ------------------------------------


def test_every_d3_kind_carries_the_same_hover_layer():
    """The sankey was drawn last and carried only the browser's own
    <title>: a tooltip that waits a second, cannot be styled, and never
    appears on a touch screen at all. Four kinds had the real one and the
    fifth did not, which is not a decision anybody made -- so assert the
    set, not the sankey."""
    js = _chart_js()
    for kind in (
        "renderDonut",
        "renderChord",
        "renderArc",
        "renderStoryMap",
        "renderSankey",
    ):
        body = js[js.index(f"function {kind}(") :]
        body = body[: body.index("\n  function ")]
        assert "tooltip(el)" in body, f"{kind} has no tooltip"
        assert "interactive(" in body, f"{kind} has no hover layer"
        assert (
            'append("title")' not in body
        ), f"{kind} draws a native tooltip as well as the real one"


def test_the_sankey_isolates_a_flow_from_the_ones_crossing_it():
    """A diagram of eighty crossing bands is unreadable without it, and
    isolation is the one thing a native <title> cannot do."""
    js = _chart_js()
    body = js[js.index("function renderSankey(") :]
    body = body[: body.index("\n  function ")]
    # A band isolates itself; a block and its label isolate every band
    # touching them.
    assert "group: bands" in body
    assert "band.source === node || band.target === node" in body
    assert body.count("interactive(") == 3, "a mark stopped answering"


def test_a_stacked_segment_says_what_it_is_a_share_of():
    """A stack is a part-to-a-whole claim, and the hover said one half of
    it.

    On a percent stack it was worse than incomplete: `offset: "expand"`
    replaces the value with a fraction of its column, so the tip read
    "Articles (%) 42" and the 420 articles behind it appeared nowhere on
    the chart. There is no way to tell a small share of a large county
    from a large share of a small one.

    The donut has always reported both -- `tipRow(ylabel, value)` beside
    `tipRow("share", ...)` -- and this is the same claim drawn as
    rectangles.
    """
    assert "function shareInTip(" in JS
    # The count and the share in one line, not two: a bare fraction beside
    # the number it came from reads as two numbers that disagree.
    assert "toLocaleString()} (${share.toFixed(1)}%)" in JS
    assert "format: { [axis]: false }" in JS

    # Applied where marks compose a whole -- a stacked bar and a stacked
    # area -- and nowhere else.
    assert 'shareInTip(enc, rows, x, y, horizontal ? "x" : "y")' in JS
    assert 'if (series) shareInTip(enc, rows, x, y, "y");' in JS
    # One series is not a composition, so both are behind a series check.
    assert "if (series && config.stacked !== false) {" in JS


def test_the_tip_options_survive_the_marks_defaults():
    """`common` carries `tip: true`. Spread after the encoding it replaced
    the tip options a percent stack sets, so the line naming what the
    share is a share of appeared beside the fraction it replaces rather
    than instead of it -- two numbers, one of them unexplained."""
    assert "{ ...common, ...enc, rx: 2 }" in JS
    assert "{ ...enc, ...common, rx: 2 }" not in JS
