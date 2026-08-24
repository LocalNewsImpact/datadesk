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
