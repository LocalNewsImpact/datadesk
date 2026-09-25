"""A report is not a list of rows.

The byline report is one row per byline with several publications under it,
and those publications belong to owners: six Rust Communications titles under
one byline are ONE owner, not six. Flattened into a plain table it is six rows
repeating the byline; rendered as two lists it says which publications and
which owners and never which owner ran which publication.

So the query returns long form -- subject, item, group, value -- and the type
nests it twice.
"""

from pathlib import Path

from visuals.builder import CHART_KINDS, CHART_LIBS
from visuals.types import BY_ID

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "static/js/datadesk-chart.js"
CSS = ROOT / "static/css/datadesk.css"
FORM = ROOT / "templates/visuals/builder_edit.html"
BUILDER = ROOT / "visuals/builder.py"


def _roster():
    source = CHART.read_text()
    start = source.index("function renderRoster")
    return source[start : source.index("function renderTable", start)]


class TestTheTypeIsReachable:
    def test_the_builder_accepts_it(self):
        assert "roster" in CHART_KINDS

    def test_it_declares_the_four_roles_it_nests_on(self):
        roles = [r.id for r in BY_ID["roster"].roles]
        assert roles == ["subject", "item", "item_group", "item_value"]

    def test_every_role_has_a_control(self):
        """An option the form does not offer is an option nobody can set --
        which is how `bands` sat declared and unreachable."""
        form = FORM.read_text()
        for role in BY_ID["roster"].roles:
            assert f'name="{role.id}"' in form, role.id

    def test_its_settings_persist(self):
        source = BUILDER.read_text()
        for key in ("subject", "item_group", "item_value", "roster_draw"):
            assert f'"{key}",' in source, key
        assert "roster_no_search" in source
        assert "roster_no_total" in source

    def test_it_draws_in_plain_dom(self):
        """A roster is a table. Loading Plot and d3 to draw one is the cost
        the library table was added to avoid."""
        assert CHART_LIBS["roster"] == ()


class TestItNestsTwice:
    def test_items_group_under_their_group(self):
        assert "function grouped(" in _roster()

    def test_a_group_reserves_its_height_in_both_columns(self):
        """The group name sits level with the first item it owns. Without
        this it drifts down the block and the pairing stops reading."""
        body = _roster()
        assert 'cell.style.setProperty("--n", block.items.length)' in body
        css = CSS.read_text()
        assert ".dd-roster-grp" in css
        assert "min-height: calc(var(--n) * 22px" in css

    def test_both_columns_emit_the_same_groups(self):
        """One function renders both, so they cannot fall out of order."""
        body = _roster()
        assert body.count("function chipsFor(") == 1
        assert "chipsFor(s, col.pair)" in body


class TestTheDropdownIsTheData:
    def test_its_options_are_the_groups_the_query_returned(self):
        """No second configuration to fall out of step with the SQL."""
        body = _roster()
        assert "new Set(rows.map((r) => String(r[group]" in body

    def test_it_is_absent_when_no_group_is_chosen(self):
        body = _roster()
        assert "if (group) {" in body


class TestWhatItRefusesToDraw:
    def test_it_says_what_is_missing_rather_than_drawing_nothing(self):
        """A chart that renders blank with no explanation is how a county
        map cost an afternoon."""
        body = _roster()
        assert "if (!subject || !item)" in body
        assert "Pick the column to make one row per" in body


class TestTheDrawCap:
    def test_the_cap_is_on_drawing_only(self):
        """Filtering and the export take every matching row; the cap is
        about first paint."""
        body = _roster()
        assert "order.slice(0, draw)" in body
        assert "exportBar(el, rows," in body

    def test_every_row_is_an_option(self):
        body = _roster()
        assert 'config.roster_draw === "0"' in body
        assert "Infinity" in body

    def test_it_defaults_to_four_hundred(self):
        assert "parseInt(config.roster_draw, 10) || 400" in _roster()


class TestItDoesNotCountWhatTheChipsAlreadyShow:
    """A first version carried "how many items" and "how many groups" beside
    the total. On a byline report both read 1 and 1 on almost every row --
    most reporters file at one newsroom -- so it was two columns of ones,
    with labels pluralised by machine ("Publisher names").

    The chips say how many there are. The eye counts three lines faster than
    it reads the number 3.
    """

    def test_there_is_no_machine_pluralised_label(self):
        assert 'labelOf(item) + "s"' not in _roster()
        assert 'labelOf(group) + "s"' not in _roster()

    def test_the_counts_survive_as_the_sort(self):
        """Sorting the publications column still answers "who appears in the
        most places" -- the column is gone, the question is not."""
        body = _roster()
        items = '{ label: labelOf(item), pair: "item", sort: (s) => s.items.length }'
        assert items in body
        assert 'pair: "group", sort: (s) => s.groups }' in body

    def test_four_columns_at_most(self):
        """Subject, total, items, groups. A roster with no value role or no
        group drops those and keeps the rest."""
        body = _roster()
        block = body[body.index("const cols = [") : body.index("].filter(Boolean);")]
        assert block.count("label:") == 4


class TestACheckboxNamesTheException:
    """The builder stores a checkbox only when it is ticked; unticked writes
    nothing. A flag meaning "on" therefore can never be turned off -- which
    is how "Offer a search box" shipped as a control that changed no pixel:
    it started unticked, stored nothing, and the renderer read nothing as on.
    """

    def test_an_unticked_box_stores_nothing(self):
        from django.http import QueryDict

        from visuals.builder import config_from_form

        config = config_from_form(QueryDict("kind=roster&subject=a&item=b"))
        assert "roster_no_total" not in config
        assert "roster_no_search" not in config

    def test_a_ticked_box_stores_true(self):
        from django.http import QueryDict

        from visuals.builder import config_from_form

        config = config_from_form(
            QueryDict(
                "kind=roster&subject=a&item=b&roster_no_total=1&roster_no_search=1"
            )
        )
        assert config["roster_no_total"] is True
        assert config["roster_no_search"] is True

    def test_nothing_stored_means_the_default(self):
        """So a roster saved before either setting existed -- and one saved
        with both boxes unticked -- totals and offers search."""
        body = _roster()
        assert "config.roster_no_total !== true" in body
        assert "config.roster_no_search !== true" in body

    def test_no_flag_reads_absence_as_off(self):
        """The shape that cannot be switched: `!== false` on a key the
        builder never writes as false."""
        body = _roster()
        assert "!== false" not in body


class TestTheTotalOnlyWhereNumbersAdd:
    def test_the_total_column_depends_on_the_setting(self):
        body = _roster()
        assert 'value && totals ? { label: "Total"' in body

    def test_without_a_total_the_subject_sorts_first(self):
        """With no total the first column is the owner, and an owner sorts
        alphabetically rather than largest-first."""
        body = _roster()
        assert "let sortAt = value && totals ? 1 : 0;" in body
        assert "let dir = value && totals ? -1 : 1;" in body


class TestUniqueBylinesIsAMeasure:
    """The byline report's newsroom view counts unique bylines per
    publication, and the corpus could count articles and publishers but not
    that -- so the report could not be rebuilt as a visual at all."""

    def test_it_is_offered(self):
        from visuals.corpus import MEASURES

        assert MEASURES["bylines"]["label"] == "Unique bylines"

    def test_it_counts_each_byline_once(self):
        from django.db.models import Count

        from visuals.corpus import MEASURES

        agg = MEASURES["bylines"]["agg"]()
        assert isinstance(agg, Count)
        assert agg.distinct is True

    def test_it_is_not_recombined_across_groups(self):
        """A reporter filing for two papers is one byline at each and one
        person overall. Summing group counts double-counts them, so the
        pivot refuses it with a rollup, as it does distinct publishers."""
        from visuals.corpus import MEASURES

        assert MEASURES["bylines"]["combine"] is None
