"""A locator map, and a visual you can delete.

THE LOCATOR MAP exists because the question "which counties are in this
market" has no number in it. Asked of a choropleth it has to be faked -- a
column of 1s and a legend reading 0.143 to 1.0 -- and the reader is shown a
scale that means nothing.

Its geography is READ FROM THE CODE, not chosen. The choropleth makes that
a setting, `geo_level`, which lives in `types.UNDRAWN` and so is never
rendered by the builder's own steps; its default is `states`, so a county
map built from 5-digit FIPS joins nothing and draws blank, with no error
and nothing on the page to suggest why. That cost an afternoon on
2026-09-14. A GEOID's length already says what it is.

DELETE exists because there was no way to remove a visual at all. Five
drafts accumulated in one session from a builder that 500'd on save, and
clearing them needed direct SQL against production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry
from visuals.models import Visual
from visuals.types import BY_ID

CHART_JS = Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"


# --- the type ----------------------------------------------------------------


class TestTheLocatorAsksForOneThing:
    def test_it_takes_areas_and_no_value(self):
        """One required role. A value column would be the choropleth."""
        chart = BY_ID["locator"]
        assert [r.id for r in chart.roles] == ["area"]
        assert chart.roles[0].needs is True
        assert "geo" in chart.roles[0].accepts

    def test_it_offers_no_palette_or_scale(self):
        """Nothing to shade by means nothing to scale. A palette control on
        this type would be a promise the renderer cannot keep."""
        options = {o.id for o in BY_ID["locator"].options}
        assert "geo_palette" not in options
        assert "geo_value" not in options

    def test_it_does_not_ask_which_geography(self):
        """THE FIX FOR THE BLANK MAP. `geo_level` is not an option here: the
        renderer reads the level off the code's length, so there is no
        setting to leave on its wrong default."""
        assert "geo_level" not in {o.id for o in BY_ID["locator"].options}

    def test_every_option_it_declares_can_be_drawn(self):
        """`UNDRAWN` is the set of options declared but never rendered by
        the builder -- a setting somebody sets and believes they have. A new
        type must not add to it."""
        from visuals.types import UNDRAWN

        assert not {o.id for o in BY_ID["locator"].options} & UNDRAWN

    def test_its_choices_have_values_to_offer(self):
        """A "choice" with no values renders as an empty control, which is
        one reason `geo_level` never reached the page."""
        for option in BY_ID["locator"].options:
            if option.kind == "choice":
                assert option.values, f"{option.id} has nothing to offer"


# --- the level, read from the code -------------------------------------------


def _level_table():
    """`LEVEL_BY_LENGTH` as the renderer declares it.

    Read out of the JS rather than restated here: the point of the table is
    that it agrees with `GEO_LEVELS`, and a copy in the test would agree
    with itself while the page drew the wrong geography.
    """
    source = CHART_JS.read_text()
    match = re.search(r"const LEVEL_BY_LENGTH = \{([^}]*)\}", source)
    assert match, "LEVEL_BY_LENGTH is gone from the renderer"
    return {int(k): v for k, v in re.findall(r"(\d+):\s*\"(\w+)\"", match.group(1))}


class TestTheGeographyIsReadFromTheCode:
    def test_each_length_maps_to_its_level(self):
        assert _level_table() == {
            2: "states",
            5: "counties",
            7: "places",
            11: "tracts",
        }

    def test_the_lengths_agree_with_the_boundary_loader(self):
        """THE ASSERTION THAT MATTERS. `GEO_LEVELS` tells the loader how
        long an id is at each level; `LEVEL_BY_LENGTH` reads the level back
        off the id. If they ever disagree, the map joins nothing -- which is
        exactly the failure this type exists to avoid, reintroduced from the
        other side."""
        source = CHART_JS.read_text()
        lengths = {
            level: int(n)
            for level, n in re.findall(r"(\w+): \{[^}]*idLength: (\d+)", source)
        }
        for length, level in _level_table().items():
            assert (
                lengths[level] == length
            ), f"{level} is idLength {lengths[level]} but read from length {length}"

    def test_a_five_digit_code_is_a_county(self):
        """The case that drew a blank map: 29019 is Boone County, Missouri,
        and under the choropleth's `states` default it matched nothing."""
        assert _level_table()[len("29019")] == "counties"


# --- deleting ----------------------------------------------------------------


@pytest.fixture
def designer(client, django_user_model):
    user = django_user_model.objects.create_user(
        "designer", email="designer@localnewsimpact.org"
    )
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
    client.force_login(user)
    return user


@pytest.fixture
def draft(designer):
    return Visual.objects.create(
        slug="a-draft",
        title="A draft",
        source_kind="inline",
        template="builder",
        config={"kind": "locator"},
        created_by=designer,
    )


@pytest.mark.django_db
class TestDeletingAVisual:
    def test_a_draft_is_deleted(self, client, draft):
        response = client.post(reverse("visuals:builder_delete", args=[draft.slug]))
        assert response.status_code == 302
        assert not Visual.objects.filter(slug=draft.slug).exists()

    def test_it_is_written_to_the_audit_log(self, client, draft):
        """What was removed and by whom. A delete that leaves no trace is
        indistinguishable from a visual that never existed."""
        client.post(reverse("visuals:builder_delete", args=[draft.slug]))
        entry = AuditLogEntry.objects.get(action="visual:delete")
        assert entry.target_ids == [draft.slug]
        assert entry.before["title"] == "A draft"
        assert entry.before["kind"] == "locator"

    def test_a_get_does_nothing(self, client, draft):
        """A link that destroys records is one a crawler or a link prefetch
        can trip. POST only."""
        response = client.get(reverse("visuals:builder_delete", args=[draft.slug]))
        assert response.status_code == 404
        assert Visual.objects.filter(slug=draft.slug).exists()

    def test_a_published_visual_is_refused(self, client, draft):
        """Something may be embedding it, and an embed that 404s is a hole
        in somebody else's page. Unpublish first -- that is reversible."""
        draft.status = Visual.PUBLISHED
        draft.save(update_fields=["status"])
        response = client.post(reverse("visuals:builder_delete", args=[draft.slug]))
        assert response.status_code == 302
        assert Visual.objects.filter(slug=draft.slug).exists()

    def test_a_viewer_may_not_delete(self, client, draft, django_user_model):
        other = django_user_model.objects.create_user(
            "nosy", email="nosy@localnewsimpact.org"
        )
        Grant.objects.create(user=other, app=DATADESK, scope="", role="viewer")
        client.force_login(other)
        client.post(reverse("visuals:builder_delete", args=[draft.slug]))
        assert Visual.objects.filter(slug=draft.slug).exists()


# --- the way back ------------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_advanced_settings_links_back_to_the_editor(client, designer, draft):
    """Advanced settings is reached FROM the builder, so the way back has to
    be the builder. Without it the only exit abandons the visual being
    edited, which is what made the page feel like a dead end."""
    page = client.get(reverse("visuals:builder_edit", args=[draft.slug]))
    assert page.status_code == 200
    body = page.content.decode()
    assert reverse("visuals:builder_step", args=[draft.slug, "fields"]) in body


# --- advanced settings, organised as the builder asks -------------------------


ADVANCED = (
    Path(__file__).resolve().parent.parent / "templates/visuals/builder_edit.html"
)


class TestAdvancedSettingsFollowsTheWalk:
    """It was grouped by the code that consumes the settings -- "Mapping",
    "Flows", "Map (GIS)", "Style", "Text" -- with the title and theme last
    and the data source below those. Nothing said which control changed
    what, and finding a setting meant reading every section.

    Each legend now names the builder step it belongs to, and the Look
    groups come before the Fields groups, as the walk asks them.

    The fieldsets themselves are left WHOLE. Each one's `data-kinds` is
    what hides it for the wrong chart, and two of them hold controls that
    share a name -- a scatter's `size` and a dot map's -- which stays
    correct only while the fieldsets stay separate and the hidden one is
    disabled."""

    def _page(self):
        return ADVANCED.read_text()

    def test_the_legends_name_the_builder_step(self):
        legends = re.findall(r"<legend>([^<]*)</legend>", self._page())
        numbered = [lg.split("·")[0].strip() for lg in legends]
        assert numbered[:3] == ["1", "2", "2"], numbered
        assert "Chart" in legends[0]
        assert all("Look" in lg for lg in legends[1:3])

    def test_look_comes_before_fields(self):
        """A title is not an advanced setting, and it was the last thing on
        the page."""
        page = self._page()
        assert page.index("2 · Look — the words") < page.index("4 · Fields")

    def test_every_kind_is_still_gated(self):
        """THE REGRESSION THIS CAUGHT. Flattening the fieldsets into step
        groups dropped their `data-kinds`, which would have shown the flow
        map's controls on every chart. The gating lives on the fieldset, so
        the fieldset has to survive the reorganising."""
        gated = set()
        for group in re.findall(r'data-kinds="([^"]+)"', self._page()):
            gated.update(group.split())
        for kind in ("chord", "arc", "flowmap", "choropleth", "points", "locator"):
            assert kind in gated, f"{kind} lost its gate"

    def test_the_two_size_controls_both_survive(self):
        """A scatter's dot size and a dot map's are different settings with
        the same name, in different fieldsets. Merging the sections keeps
        one and silently loses the other."""
        form = self._page()
        form = form[
            form.index('<form method="post" id="config-form">') : form.index(
                '<button type="submit">Save</button>'
            )
        ]
        assert re.findall(r'name="size"', form).__len__() == 2

    def test_it_stays_one_form(self):
        """The view rebuilds `config` from what was submitted, so a second
        form covering half the settings clears the other half on save --
        which is what made settings look as though they would not
        persist."""
        page = self._page()
        form = page[
            page.index('<form method="post" id="config-form">') : page.index(
                '<button type="submit">Save</button>'
            )
        ]
        assert form.count("<form") == 1


# --- it looks like the other maps --------------------------------------------


class TestItIsDrawnLikeAStoryMap:
    """A locator and a story map are the same kind of picture and looked
    like two different products.

    The first version fitted a projection to the highlighted areas and drew
    everything else in the file behind them, so Kansas and Illinois arrived
    around Missouri -- they were in the national counties topojson and
    nothing had excluded them. The story map filters its features to the
    frame instead, and that is the difference."""

    def _locator(self):
        source = CHART_JS.read_text()
        start = source.index("function renderLocator")
        return source[start : source.index("function renderMap", start)]

    def test_it_filters_to_the_frame(self):
        """Dropped, not cropped. A projection fitted to the data still
        draws every other feature the file holds."""
        body = self._locator()
        assert "areas.filter(inFrame)" in body
        assert "const inFrame" in body

    def test_it_uses_the_same_palette_as_the_other_maps(self):
        """`missing` for the basemap and `boundary` for its lines are what
        an unshaded county gets on a story map. A locator inventing its own
        greys is how two charts of the same thing stop matching."""
        body = self._locator()
        assert "t.missing" in body
        assert "t.boundary" in body
        assert "t.seqHigh" in body

    def test_it_keeps_the_house_aspect(self):
        """0.62, as the story map sets it. Left to the container a map
        reflows to whatever height the data's bounding box implies, and two
        maps of the same counties come out different shapes."""
        assert "width * 0.62" in self._locator()

    def test_it_says_so_when_nothing_matched(self):
        """A code that matches no boundary is the failure that looks like a
        working map with nothing on it -- which is how a 5-digit FIPS drawn
        at state level presented. Say it instead."""
        assert "None of those codes matched" in self._locator()


class TestHoveringNamesThePlace:
    """Hovering a county said "29019". That is the join key, not an answer
    to the question somebody is asking by hovering."""

    def _locator(self):
        source = CHART_JS.read_text()
        start = source.index("function renderLocator")
        return source[start : source.index("function renderMap", start)]

    def test_the_tooltip_uses_the_name(self):
        body = self._locator()
        assert "title: nameOf" in body

    def test_the_name_falls_back_to_the_boundary_file(self):
        """`counties-10m.json` carries `properties.name` for every county --
        `{"id": "04015", "properties": {"name": "Mohave"}}` -- so an upload
        that is a bare list of codes still hovers as a place."""
        body = self._locator()
        assert "f.properties.name" in body

    def test_the_data_column_wins(self):
        """Where the file has its own name column, that spelling is the one
        whoever made the file chose."""
        body = self._locator()
        assert body.index("labelBy.get(String(f.id))") < body.index("f.properties.name")

    def test_the_tooltip_does_not_wait_for_the_labels_toggle(self):
        """That toggle decides whether names are DRAWN on the map. A
        tooltip is not a label, and gating one on the other is why a
        hover showed a code."""
        body = self._locator()
        names_built = body.index("const labelBy = new Map();")
        toggle = body.index("config.locator_labels")
        assert names_built < toggle, "labels are still built inside the toggle"


# --- the label plate ----------------------------------------------------------


class TestALabelPlateIsNotAHighlight:
    """The plate behind each county label was drawn in the highlight colour,
    `t.seqHigh`. That is wrong twice over, and the second way is the one that
    makes the map lie.

    Over the county it names the plate is the same colour as the fill, so it
    does nothing -- the label has no ground and the whole mechanism is inert.
    And a county label is almost always WIDER than its county on a state map,
    so the plate overhangs onto the basemap, where a `seqHigh` rectangle reads
    as one more highlighted area. A map of 14 newsrooms showed coloured blocks
    over counties that hold none.

    Highlight colour is the map's one piece of data encoding: it means "in the
    set". A label is not in the set, so it may not wear that colour. The plate
    is the page surface, and the ink on it is `t.ink`.
    """

    def _plate(self):
        source = CHART_JS.read_text()
        start = source.index("function plateLabels")
        return source[start : source.index("function renderMap", start)]

    def _locator(self):
        source = CHART_JS.read_text()
        start = source.index("function renderLocator")
        return source[start : source.index("function renderMap", start)]

    def test_the_plate_is_the_page_surface(self):
        assert 'plate.setAttribute("fill", t.surface)' in self._plate()

    def test_the_plate_is_never_the_highlight_colour(self):
        """The regression to guard. `t.seqHigh` anywhere in the plate is the
        bug coming back."""
        assert "t.seqHigh" not in self._plate()

    def test_the_plate_has_a_hairline_edge(self):
        """Surface on basemap grey is a low-contrast pair in light mode; the
        edge is what keeps the plate a shape rather than a smudge. Hairline,
        so it does not compete with the county lines it crosses."""
        plate = self._plate()
        assert 'plate.setAttribute("stroke", t.boundary)' in plate
        assert 'plate.setAttribute("stroke-width", 0.5)' in plate

    def test_the_label_ink_is_the_theme_ink(self):
        """On the page ground there is no luminance test to make: `t.ink` is
        correct against `t.surface` in both themes by construction. Inking
        against the highlight instead was what forced `inkOn` here."""
        assert "fill: t.ink," in self._locator()

    def test_the_label_ink_is_not_measured_against_the_highlight(self):
        assert "inkOn(t.seqHigh)" not in self._locator()

    def test_the_plate_goes_behind_the_text(self):
        """`insertBefore` in document order is behind in paint order. A plate
        appended after the label covers the word it exists to support."""
        assert "insertBefore(plate, node)" in self._plate()

    def test_the_plate_is_measured_not_guessed(self):
        """Its width is the rendered width of the word in the reader's own
        font, which only `getBBox` knows."""
        assert "node.getBBox()" in self._plate()

    def test_the_plate_carries_the_label_transform(self):
        """Plot gives each label its own `transform`, so `getBBox` reports a
        box in that label's local space. A sibling rect without the transform
        lands at the figure's corner -- which is where every plate stacked up
        on the first attempt."""
        plate = self._plate()
        assert 'node.getAttribute("transform")' in plate
        assert 'plate.setAttribute("transform", placement)' in plate

    def test_the_figure_is_mounted_before_the_plates_are_measured(self):
        """`getBBox` on a detached node reports zeroes, and a zero-width box
        is skipped -- so measuring first plates nothing, silently."""
        body = self._locator()
        mounted = body.index("el.replaceChildren(figure)")
        assert mounted < body.index("plateLabels(figure")
