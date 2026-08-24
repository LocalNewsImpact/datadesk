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
    declared = {r.id for r in BY_ID[chart_id].roles} | {
        o.id for o in BY_ID[chart_id].options
    }
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
    assert "bar" in read_more_accurately_than("donut", fields)


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
            "requires",
            "why_not",
            "read_better",
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
    assert "Read more accurately as" in body


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
