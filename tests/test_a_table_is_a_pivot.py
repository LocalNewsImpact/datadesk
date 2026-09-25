"""A table is a pivot table.

Two lists everyone already knows: ROWS, whose order decides what nests under
what, and VALUES, the numbers beside them. The byline report's newsroom view
is Owner -> Newsroom with Unique bylines and Articles beside each, which
needs two values from one table -- and a pivot emits one measure per query.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from accounts.access import ALL_SCOPES
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def report(crawler_schema):
    """Two owners, three newsrooms, and bylines arranged so the two values
    disagree: Gray's KCTV5 has three stories by two reporters."""
    dataset = Dataset.objects.create(id="d1", slug="mizzou", label="Missouri")
    rooms = {
        "kctv5": ("KCTV5", "Gray Television"),
        "ky3": ("KY3", "Gray Television"),
        "semo": ("Southeast Missourian", "Rust Communications"),
    }
    sources = {}
    for i, (key, (name, owner)) in enumerate(rooms.items()):
        sources[key] = Source.objects.create(
            id=f"s{i}",
            host=f"{key}.example",
            host_norm=f"{key}.example",
            canonical_name=name,
            owner=owner,
        )
        DatasetSource.objects.create(id=f"ds{i}", dataset=dataset, source=sources[key])
    stories = [
        ("kctv5", "Sarah Motter"),
        ("kctv5", "Sarah Motter"),
        ("kctv5", "Greg Dailey"),
        ("ky3", "Christopher Replogle"),
        ("semo", "Aaron Horrell"),
    ]
    for i, (key, author) in enumerate(stories):
        link = CandidateLink.objects.create(
            id=f"cl{i}",
            url=f"https://{key}.example/{i}",
            source=sources[key],
            dataset_id="d1",
        )
        Article.objects.create(
            id=f"a{i}",
            candidate_link=link,
            dataset_id="d1",
            url=link.url,
            author=author,
            status="enriched",
            publish_date=dt.datetime(2026, 3, 10, tzinfo=dt.UTC),
            created_at=dt.datetime(2026, 3, 10, tzinfo=dt.UTC),
        )
    return dataset


def _spec(**extra):
    return {
        "shape": "grouped",
        "dimensions": ["owner", "publisher_name"],
        "measures": ["bylines", "articles"],
        "measure": "bylines",
        **extra,
    }


class TestSeveralValuesFromOneTable:
    def test_both_values_arrive_on_every_row(self, report):
        from visuals.corpus import run_values

        rows, meta = run_values(_spec(), ALL_SCOPES)
        by_room = {r["Publisher name"]: r for r in rows}
        assert by_room["KCTV5"]["Unique bylines"] == 2
        assert by_room["KCTV5"]["Articles"] == 3
        assert by_room["KY3"]["Unique bylines"] == 1
        assert by_room["KY3"]["Articles"] == 1

    def test_the_values_are_named_in_the_order_chosen(self, report):
        from visuals.corpus import run_values

        _rows, meta = run_values(_spec(), ALL_SCOPES)
        assert [m["label"] for m in meta["measures"]] == ["Unique bylines", "Articles"]

    def test_one_value_is_exactly_what_it_was(self, report):
        """Nothing that asks for one value changes."""
        from visuals.corpus import run_spec, run_values

        one = {"shape": "grouped", "dimensions": ["owner"], "measure": "articles"}
        assert run_values(one, ALL_SCOPES)[0] == run_spec(one, ALL_SCOPES)[0]


class TestASpecSavedBeforeThisExisted:
    def test_a_lone_measure_is_a_list_of_one(self):
        from visuals.corpus import measures_of

        assert measures_of({"measure": "articles"}) == ["articles"]

    def test_nothing_means_articles(self):
        from visuals.corpus import measures_of

        assert measures_of({}) == ["articles"]

    def test_a_value_chosen_twice_is_one_column(self):
        from visuals.corpus import measures_of

        assert measures_of({"measures": ["articles", "articles", "bylines"]}) == [
            "articles",
            "bylines",
        ]


# --- the fields step ---------------------------------------------------------


@pytest.fixture
def author(client):
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant

    user = User.objects.create_user("designer", email="d@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return user


@pytest.fixture
def table(author, crawler_schema):
    from visuals.models import Visual

    return Visual.objects.create(
        slug="pivot",
        title="Pivot",
        template="builder",
        source_kind="corpus",
        created_by=author,
        config={"kind": "table"},
    )


def _fields(client, visual, data=None):
    url = f"/visuals/builder/{visual.slug}/step/fields/"
    return client.post(url, data) if data is not None else client.get(url)


class TestTheFieldsStep:
    def test_rows_are_saved_in_the_order_they_are_set(self, client, table):
        _fields(
            client,
            table,
            {"row": ["publisher_name", "owner", ""], "value": ["articles", ""]},
        )
        table.refresh_from_db()
        assert table.spec["dimensions"] == ["publisher_name", "owner"]

    def test_several_values_are_saved_in_order(self, client, table):
        _fields(
            client,
            table,
            {"row": ["owner", "publisher_name"], "value": ["bylines", "articles"]},
        )
        table.refresh_from_db()
        assert table.spec["measures"] == ["bylines", "articles"]
        # The first value is still the measure, so everything that reads one
        # measure keeps reading the one it did.
        assert table.spec["measure"] == "bylines"

    def test_a_blank_slot_removes_what_was_in_it(self, client, table):
        _fields(client, table, {"row": ["owner", "", "publisher_name"]})
        table.refresh_from_db()
        assert table.spec["dimensions"] == ["owner", "publisher_name"]

    def test_a_row_picked_twice_counts_once(self, client, table):
        _fields(client, table, {"row": ["owner", "owner"], "value": ["articles"]})
        table.refresh_from_db()
        assert table.spec["dimensions"] == ["owner"]

    def test_no_value_means_articles(self, client, table):
        _fields(client, table, {"row": ["owner"], "value": [""]})
        table.refresh_from_db()
        assert table.spec["measures"] == ["articles"]

    def test_a_measure_is_not_a_row(self, client, table):
        _fields(client, table, {"row": ["articles"], "value": ["articles"]})
        table.refresh_from_db()
        assert not table.spec.get("dimensions")

    def test_a_category_is_not_a_value(self, client, table):
        _fields(client, table, {"row": ["owner"], "value": ["owner"]})
        table.refresh_from_db()
        assert not table.spec.get("measures")

    def test_the_old_column_checkboxes_still_save(self, client, table):
        """A form open in a tab from before the change posts `columns`."""
        _fields(client, table, {"columns": ["owner"], "measure": "articles"})
        table.refresh_from_db()
        assert table.spec["dimensions"] == ["owner"]
        assert table.spec["measures"] == ["articles"]

    def test_the_step_shows_each_row_in_its_slot_and_one_empty(self, client, table):
        table.spec = {
            "dimensions": ["owner", "publisher_name"],
            "measures": ["bylines", "articles"],
            "measure": "bylines",
        }
        table.save()
        body = _fields(client, table).content.decode()
        assert body.count('name="row"') == 3, "two chosen, one to add"
        assert body.count('name="value"') == 3
        assert 'aria-label="Row 1"' in body and 'aria-label="Row 3"' in body
        first = body.index('aria-label="Row 1"')
        second = body.index('aria-label="Row 2"')
        assert body.index('value="owner" selected', first) < second
        assert "Add a row" in body and "Remove this row" in body

    def test_a_table_saved_before_values_shows_its_one_measure(self, client, table):
        table.spec = {"dimensions": ["owner"], "measure": "bylines"}
        table.save()
        body = _fields(client, table).content.decode()
        start = body.index('aria-label="Value 1"')
        end = body.index("</select>", start)
        assert 'value="bylines" selected' in body[start:end]


# --- the renderer ------------------------------------------------------------

CHART = Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"

NEWSROOMS = [
    {
        "Owner": "Rust Communications",
        "Newsroom": "semissourian.com",
        "Unique bylines": 40,
        "Articles": 90,
    },
    {
        "Owner": "Gray Television",
        "Newsroom": "www.ky3.com",
        "Unique bylines": 25,
        "Articles": 321,
    },
    {
        "Owner": "Gray Television",
        "Newsroom": "www.kctv5.com",
        "Unique bylines": 50,
        "Articles": 503,
    },
    {
        "Owner": "Rust Communications",
        "Newsroom": "standard-democrat.com",
        "Unique bylines": 8,
        "Articles": 400,
    },
    {
        "Owner": None,
        "Newsroom": "unowned.example",
        "Unique bylines": 99,
        "Articles": 999,
    },
]


def _node(expression):
    """Evaluate `expression` against the renderer, in node, and return it."""
    import os
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:
        pytest.skip("no node to run the runtime in")
    harness = f"""
    global.window = global;
    global.document = {{
      addEventListener() {{}}, querySelectorAll: () => [],
      documentElement: {{ dataset: {{ theme: "light" }} }},
    }};
    global.matchMedia = () => ({{ matches: false }});
    {CHART.read_text()}
    const T = DatadeskChart.__test;
    console.log(JSON.stringify({expression}));
    """
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(harness)
        where = fh.name
    try:
        done = subprocess.run([node, where], capture_output=True, text=True)
    finally:
        os.unlink(where)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _order(col, num, direction, outer, rows=NEWSROOMS):
    """Run the renderer's own sort on `rows`; the newsrooms, in order."""
    return _node(
        f"T.orderRows({json.dumps(rows)}, {json.dumps(col)}, {json.dumps(num)}, "
        f"{direction}, {json.dumps(outer)}).map((r) => r.Newsroom)"
    )


class TestSortingKeepsAGroupTogether:
    def test_flat_is_an_ordinary_sort(self):
        order = _order("Articles", True, -1, [])
        assert order[:3] == [
            "unowned.example",
            "www.kctv5.com",
            "standard-democrat.com",
        ]

    def test_a_group_is_placed_by_its_best_member(self):
        """Gray's best is 503 and Rust's is 400: Gray first, and each owner's
        newsrooms together, largest first inside it. The newsroom with no
        owner is a group of its own and is placed by its 999 like any other."""
        order = _order("Articles", True, -1, ["Owner"])
        assert order == [
            "unowned.example",
            "www.kctv5.com",
            "www.ky3.com",
            "standard-democrat.com",
            "semissourian.com",
        ]

    def test_not_by_a_total(self):
        """Ascending by unique bylines: Rust's smallest is 8 and Gray's is 25,
        so Rust leads. A total would put Rust (48) ahead of Gray (75) too, so
        the case that separates them is descending: Gray's best is 50 and
        Rust's 40, while summed bylines double-count a reporter filing for
        two papers."""
        order = _order("Unique bylines", True, 1, ["Owner"])
        assert order[:2] == ["standard-democrat.com", "semissourian.com"]
        rows = [
            {"Owner": "A", "Newsroom": "a1", "Unique bylines": 30},
            {"Owner": "A", "Newsroom": "a2", "Unique bylines": 30},
            {"Owner": "B", "Newsroom": "b1", "Unique bylines": 50},
        ]
        # A totals 60 and would lead on a sum; B's best is the largest.
        assert _order("Unique bylines", True, -1, ["Owner"], rows)[0] == "b1"

    def test_a_blank_sorts_last_both_ways(self):
        """No owner recorded is not the first owner alphabetically, nor the
        last one reversed: it is a gap, and gaps go to the bottom."""
        assert _order("Owner", False, 1, ["Owner"])[-1] == "unowned.example"
        assert _order("Owner", False, -1, ["Owner"])[-1] == "unowned.example"

    def test_sorting_by_the_group_orders_the_groups_by_name(self):
        order = _order("Owner", False, 1, ["Owner"])
        assert order[:2] == ["www.ky3.com", "www.kctv5.com"] or order[:2] == [
            "www.kctv5.com",
            "www.ky3.com",
        ]
        assert order[2:4] in (
            ["semissourian.com", "standard-democrat.com"],
            ["standard-democrat.com", "semissourian.com"],
        )

    def test_two_groups_tied_on_their_best_do_not_interleave(self):
        rows = [
            {"Owner": "A", "Newsroom": "a1", "Articles": 5},
            {"Owner": "B", "Newsroom": "b1", "Articles": 5},
            {"Owner": "A", "Newsroom": "a2", "Articles": 1},
            {"Owner": "B", "Newsroom": "b2", "Articles": 1},
        ]
        assert _order("Articles", True, -1, ["Owner"], rows) == ["a1", "a2", "b1", "b2"]

    def test_three_rows_nest_twice(self):
        """Byline, then Owner, then Newsroom: a byline's owners stay under the
        byline, and an owner's newsrooms stay under the owner."""
        rows = [
            {
                "Byline": "Steph Quinn",
                "Owner": "States Newsroom",
                "Newsroom": "missouriindependent.com",
                "Articles": 60,
            },
            {
                "Byline": "Steph Quinn",
                "Owner": "Rust",
                "Newsroom": "semo",
                "Articles": 4,
            },
            {
                "Byline": "Sherman Smith",
                "Owner": "Rust",
                "Newsroom": "dexter",
                "Articles": 18,
            },
            {"Byline": "Steph Quinn", "Owner": "Rust", "Newsroom": "sd", "Articles": 9},
        ]
        order = _order("Articles", True, -1, ["Byline", "Owner"], rows)
        assert order == ["missouriindependent.com", "sd", "semo", "dexter"]


class TestTheRendererIsToldWhichColumnsAreRows:
    def test_a_corpus_table_names_its_rows_by_their_labels(self, table):
        table.spec = {
            "dimensions": ["owner", "publisher_name"],
            "measures": ["articles"],
        }
        assert table.render_config["rows"] == ["Owner", "Publisher name"]

    def test_only_a_table_is_told(self, table):
        table.config = {"kind": "bar"}
        table.spec = {"dimensions": ["owner"]}
        assert "rows" not in table.render_config

    def test_grouping_is_off_until_ticked(self):
        from visuals.builder import config_from_form

        assert "group_rows" not in config_from_form({"kind": "table"})
        assert config_from_form({"kind": "table", "group_rows": "1"})["group_rows"]

    def test_the_look_step_offers_it_on_a_table(self):
        from visuals.types import BY_ID

        assert "group_rows" in [o.id for o in BY_ID["table"].options]

    def test_the_renderer_passes_the_config_to_the_table(self):
        source = CHART.read_text()
        passed = "renderTable(el, rows, opts && opts.credits, null, null, config)"
        assert passed in source
        assert "oneTable(rows, config)" in source


BYLINES = [
    {"Byline": "Jason Vance", "Owner": "Rust", "Newsroom": "semo", "Articles": 9},
    {"Byline": "Jason Vance", "Owner": "Rust", "Newsroom": "sd", "Articles": 4},
    {"Byline": "Jason Vance", "Owner": "Gray", "Newsroom": "ky3", "Articles": 2},
    {"Byline": "Sarah Motter", "Owner": "Gray", "Newsroom": "kctv5", "Articles": 97},
]


class TestAGroupIsOneDisplayedRow:
    """A byline filing for three papers is one row with three lines, not
    three rows: the reader is counting bylines."""

    def _stack(self, outer):
        ordered = f"T.orderRows({json.dumps(BYLINES)}, 'Articles', true, -1, " + (
            f"{json.dumps(outer)})"
        )
        return _node(
            f"T.stackRows({ordered}, {json.dumps(outer)}).map((g) => ({{"
            "name: g.name, lines: g.lines.map((l) => [l.row.Newsroom, l.opens])}))"
        )

    def test_one_row_per_byline(self):
        groups = self._stack(["Byline", "Owner"])
        assert [g["name"] for g in groups] == ["Sarah Motter", "Jason Vance"]

    def test_every_publication_is_a_line_in_it(self):
        vance = self._stack(["Byline", "Owner"])[1]
        assert [line[0] for line in vance["lines"]] == ["semo", "sd", "ky3"]

    def test_an_owner_is_named_on_its_first_line_only(self):
        """Rust opens on semo and continues on sd; Gray opens on ky3."""
        vance = self._stack(["Byline", "Owner"])[1]
        assert [line[1] for line in vance["lines"]] == [1, -1, 1]

    def test_with_one_outer_row_no_line_opens_an_inner_group(self):
        vance = self._stack(["Byline"])[1]
        assert [line[1] for line in vance["lines"]] == [-1, -1, -1]

    def test_the_cap_and_the_count_are_of_groups(self):
        source = CHART.read_text()
        assert "const shown = groups.slice(0, 500);" in source
        assert "groups.length.toLocaleString()" in source
