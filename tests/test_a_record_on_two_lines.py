"""A table whose rows are records: a line to scan and a paragraph to read.

The Missouri Press Association table was date, headline, publications,
owners and a paragraph as five columns, which squeezed the paragraph to a
few words a line. A detail column is drawn under its row across every
column above it; a column can be chips instead of a column, or hidden; a
long list shows its first few; and a date reads 09-25-2026.
"""

import json

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from tests.test_a_table_is_a_pivot import _node
from visuals.models import Visual
from visuals.services import record_snapshot

ROWS = [
    {
        "Date": "2026-09-25",
        "Headline": "[MPA elects officers](https://mopress.com/stories/x,1)",
        "Type": "Ownership or closure",
        "Publications": "A; B; C; D; E; Vandalia Leader",
        "Paragraph": "Jones bought two newspapers from Vernon Publishing.",
    },
    {
        "Date": "2026-01-02",
        "Headline": "[Notebook](https://mopress.com/stories/y,2)",
        "Type": "Mention only",
        "Publications": "Weston Chronicle",
        "Paragraph": "",
    },
]


class TestTheRenderer:
    def test_a_date_reads_american(self):
        assert _node('T.usDate("2026-09-25")') == "09-25-2026"
        assert _node('T.usDate("Sept. 2026")') == "Sept. 2026"

    def test_a_date_column_is_every_value_a_date(self):
        cols = ["Date", "Type"]
        assert _node(f"[...T.dateColumns({json.dumps(ROWS)}, {json.dumps(cols)})]") == [
            "Date"
        ]

    def test_a_list_is_semicolons(self):
        assert _node('T.listItems("A; B;C ;")') == ["A", "B", "C"]
        cols = ["Publications", "Type"]
        assert _node(
            f"[...T.listColumns({json.dumps(ROWS)}, {json.dumps(cols)}, new Set())]"
        ) == ["Publications"]

    def test_a_link_with_a_semicolon_is_one_value(self):
        rows = ROWS + [
            {
                "Headline": "[Plant to close; printing moves to Iowa]"
                "(https://www.ksmu.org/news/2025-07-09/plant)",
                "Publications": "Springfield News-Leader",
            }
        ]
        cols = ["Headline", "Publications"]
        assert _node(
            f"[...T.listColumns({json.dumps(rows)}, {json.dumps(cols)}, new Set())]"
        ) == ["Publications"]
        # Text that is not a link still splits.
        rows[-1]["Headline"] = "Plant to close; printing moves to Iowa"
        assert _node(
            f"[...T.listColumns({json.dumps(rows)}, {json.dumps(cols)}, new Set())]"
        ) == ["Headline", "Publications"]

    def test_the_layout_is_read_from_the_config(self):
        from tests.test_a_table_is_a_pivot import CHART

        js = CHART.read_text()
        assert "const detail = csvOf(config && config.detail_columns)" in js
        assert "c !== chipCol" in js
        # A match behind "+N more" opens the cell.
        assert "const all = items.length <= limit || opened.has(id) || hit;" in js
        assert "td.colSpan = cols.length;" in js


@pytest.fixture
def author(client):
    user = User.objects.create_user("designer", email="d@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return user


@pytest.mark.django_db(databases=["default", "crawler"])
class TestTheLookStep:
    def _table(self, author):
        visual = Visual.objects.create(
            slug="mpa",
            title="MPA",
            template="builder",
            source_kind="inline",
            created_by=author,
            config={"kind": "table"},
        )
        record_snapshot(visual, author, ROWS)
        return visual

    def test_the_layout_is_chosen_from_the_columns(self, client, author):
        visual = self._table(author)
        url = f"/visuals/builder/{visual.slug}/step/theme/"
        body = client.get(url).content.decode()
        assert 'name="opt-detail_columns" value="Paragraph"' in body
        assert '<option value="Type"' in body

    def test_it_is_saved(self, client, author):
        visual = self._table(author)
        client.post(
            f"/visuals/builder/{visual.slug}/step/theme/",
            {
                "opt-detail_columns": ["Paragraph"],
                "opt-filter_column": "Type",
                "opt-hidden_columns": ["Publications"],
                "opt-list_limit": "3",
            },
        )
        visual.refresh_from_db()
        config = visual.config
        assert config["detail_columns"] == "Paragraph"
        assert config["filter_column"] == "Type"
        assert config["hidden_columns"] == "Publications"
        assert config["list_limit"] == "3"

    def test_a_column_it_does_not_have_is_refused(self, client, author):
        visual = self._table(author)
        client.post(
            f"/visuals/builder/{visual.slug}/step/theme/",
            {"opt-filter_column": "Nope"},
        )
        visual.refresh_from_db()
        assert visual.config["filter_column"] == ""
