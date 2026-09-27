"""An owner's unique bylines, counted across its newsrooms.

The byline table groups newsrooms under their owner, each newsroom with its
unique bylines. The owner's own number cannot be summed from those: a
reporter filing for two Gray stations is one byline at Gray, not two. So the
pivot counts it -- for the same dates, filters and newsrooms shown -- and the
table names the group with it: "Gray Television (3)".
"""

import datetime as dt

import pytest

from accounts.access import ALL_SCOPES
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from tests.test_a_table_is_a_pivot import CHART, _node
from visuals.corpus import GROUP_BYLINES

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def corpus(crawler_schema):
    dataset = Dataset.objects.create(id="d1", slug="mizzou", label="Missouri")
    rooms = {
        "kmiz": ("KMIZ", "Gray Television"),
        "ky3": ("KY3", "Gray Television"),
        "news": ("The News", "Someone Else"),
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
        ("kmiz", "Ann Lee, Bob Ray"),
        ("ky3", "Ann Lee"),
        ("ky3", "Cy Doe"),
        ("news", "Dee Fox"),
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


def _rows(**spec):
    from visuals.corpus import run_values

    rows, _ = run_values(spec, ALL_SCOPES)
    return rows


class TestTheCount:
    def test_an_owner_counts_people_once(self, corpus):
        rows = _rows(dimensions=["owner", "publisher_name"], measure="bylines")
        gray = [r for r in rows if r["Owner"] == "Gray Television"]
        assert sum(r["Unique bylines"] for r in gray) == 4
        assert {r[GROUP_BYLINES]["Owner"] for r in gray} == {3}

    def test_it_travels_when_bylines_is_the_second_value(self, corpus):
        rows = _rows(
            dimensions=["owner", "publisher_name"], measures=["articles", "bylines"]
        )
        gray = [r for r in rows if r["Owner"] == "Gray Television"]
        assert {r[GROUP_BYLINES]["Owner"] for r in gray} == {3}

    def test_only_the_newsrooms_shown(self, corpus):
        """Filtered to one Gray station, the owner counts that station."""
        rows = _rows(
            dimensions=["owner", "publisher_name"],
            measure="bylines",
            only={"publisher_name": ["KY3"]},
        )
        assert {r[GROUP_BYLINES]["Owner"] for r in rows} == {2}

    def test_one_dimension_has_no_groups(self, corpus):
        rows = _rows(dimensions=["owner"], measure="bylines")
        assert all(GROUP_BYLINES not in r for r in rows)


class TestTheTable:
    def test_the_field_is_not_a_column(self):
        assert _node('T.columnsOf({"Owner": 1, "__group_bylines": {}})') == ["Owner"]

    def test_the_group_is_named_with_it(self):
        js = CHART.read_text()
        assert "group.lines[0]?.row?.__group_bylines?.[c]" in js
        assert "n.textContent = ` (${Number(total).toLocaleString()})`;" in js
        assert "const cols = columnsOf(rows[0]);" in js
