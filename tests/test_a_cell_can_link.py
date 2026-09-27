"""A table cell can be a link, and a paragraph gets the width to be read.

An uploaded list of MPA stories carried each story's address as a column
of its own: a column of URLs nobody reads, beside a headline nobody could
click. A cell written `[headline](https://...)` now draws as the headline,
linked. And the paragraph quoted from each story was squeezed to whatever
width the short columns left; a text column that reads as prose is given
room.
"""

import json

from tests.test_a_table_is_a_pivot import _node

STORY = "[Weston Chronicle sold](https://mopress.com/stories/september-2026,39849)"


def _call(fn, *args):
    return _node(f"T.{fn}({', '.join(json.dumps(a) for a in args)})")


def _prose(rows, cols, numeric=()):
    """The prose columns, as a list: a Set does not survive JSON."""
    return _node(
        f"[...T.proseColumns({json.dumps(rows)}, {json.dumps(cols)}, "
        f"new Set({json.dumps(list(numeric))}))]"
    )


class TestALinkInACell:
    def test_markdown_is_a_link(self):
        assert _call("cellLink", STORY) == {
            "text": "Weston Chronicle sold",
            "href": "https://mopress.com/stories/september-2026,39849",
        }

    def test_only_http_and_https(self):
        """A cell is data from a file; a `javascript:` address in it would
        run in the reader's page."""
        assert _call("cellLink", "[click](javascript:alert(1))") is None
        assert _call("cellLink", "[mail](mailto:a@b.org)") is None

    def test_plain_text_is_not_a_link(self):
        assert _call("cellLink", "Weston Chronicle") is None
        assert _call("cellLink", "see [this](https://x.org) and more") is None
        assert _call("cellLink", 42) is None

    def test_a_reader_sees_and_filters_on_the_text(self):
        assert _call("cellText", STORY) == "Weston Chronicle sold"
        assert _call("cellText", "plain") == "plain"
        assert _call("cellText", None) == ""


class TestProseGetsWidth:
    ROWS = [
        {"Date": "2026-09-01", "Paragraph": "x" * 200, "Type": "Ownership"},
        {"Date": "2026-08-01", "Paragraph": "y" * 120, "Type": "Historical"},
    ]

    def test_a_long_text_column_is_prose(self):
        cols = ["Date", "Paragraph", "Type"]
        assert _prose(self.ROWS, cols) == ["Paragraph"]

    def test_a_headline_link_is_measured_by_its_text(self):
        """The address inside a link is not text a reader reads, so a column
        of short linked headlines is not prose."""
        rows = [{"Story": f"[Short]({'https://x.org/' + 'a' * 200})"}]
        assert _prose(rows, ["Story"]) == []

    def test_a_number_column_is_never_prose(self):
        rows = [{"n": 10**100}]
        assert _prose(rows, ["n"], ["n"]) == []


class TestTheTableUsesThem:
    def test_every_cell_path_draws_through_fill_cell(self):
        from tests.test_a_table_is_a_pivot import CHART

        js = CHART.read_text()
        assert "fillCell(td, value);" in js
        assert "fillCell(div, line.row?.[c]);" in js
        assert "fillCell(td, group.name);" in js
        assert 'if (prose.has(c)) td.className = "prose";' in js
        assert "String(cellText(r?.[c])).toLowerCase().includes(needle)" in js
