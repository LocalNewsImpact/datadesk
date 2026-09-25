"""One byline per row.

The Byline column grouped `articles.author` as written, so a co-authored
story's string was one value: "Nick Gladney, Chris Regnier" on KPLR11/Fox 2
Now was one row naming two reporters, sixteen times over, and "Abby Volz,
Kerrigan Foster, Wyatt Sherwin" was one "byline" at The Arrow. 402 of March's
12,119 bylined Mizzou stories name more than one person.
"""

import datetime as dt

import pytest

from accounts.access import ALL_SCOPES
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def corpus(crawler_schema):
    dataset = Dataset.objects.create(id="d1", slug="mizzou", label="Missouri")
    rooms = {
        "fox2": ("KPLR11/Fox 2 Now", "Nexstar Media Group"),
        "arrow": ("The Arrow", "Southeast Missouri State University"),
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
        ("fox2", "Nick Gladney, Chris Regnier"),
        ("fox2", "Nick Gladney, Chris Regnier"),
        ("fox2", "Nick Gladney"),
        ("fox2", "Nick Gladney, Laura Simon"),
        ("arrow", "Abby Volz, Kerrigan Foster, Wyatt Sherwin"),
        ("arrow", "Jimmy Rea and Mavis Parks"),
        ("arrow", "Jane Doe, "),
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
    from visuals.corpus import run_spec

    rows, _ = run_spec(spec, ALL_SCOPES)
    return rows


class TestAByline:
    def test_is_one_name(self, corpus):
        rows = _rows(dimensions=["author"], measure="articles")
        names = {r["Byline"] for r in rows}
        assert "Nick Gladney, Chris Regnier" not in names
        assert {"Nick Gladney", "Chris Regnier", "Laura Simon"} <= names

    def test_counts_every_story_that_names_it(self, corpus):
        by = {r["Byline"]: r["Articles"] for r in _rows(dimensions=["author"])}
        assert by["Nick Gladney"] == 4
        assert by["Chris Regnier"] == 2

    def test_three_names_are_three_bylines(self, corpus):
        by = {r["Byline"] for r in _rows(dimensions=["author"])}
        assert {"Abby Volz", "Kerrigan Foster", "Wyatt Sherwin"} <= by

    def test_and_separates_two_people(self, corpus):
        by = {r["Byline"] for r in _rows(dimensions=["author"])}
        assert {"Jimmy Rea", "Mavis Parks"} <= by

    def test_a_trailing_comma_is_not_a_second_byline(self, corpus):
        by = [r["Byline"] for r in _rows(dimensions=["author"])]
        assert all(name.strip() for name in by)
        assert "Jane Doe" in by

    def test_sits_beside_its_newsroom(self, corpus):
        rows = _rows(dimensions=["author", "publisher_name"])
        pairs = {(r["Byline"], r["Publisher name"]) for r in rows}
        assert ("Chris Regnier", "KPLR11/Fox 2 Now") in pairs
        assert ("Wyatt Sherwin", "The Arrow") in pairs


class TestUniqueBylines:
    def test_counts_people_not_strings(self, corpus):
        """Fox 2's four stories carry three strings and three people; The
        Arrow's carry three strings and six."""
        rows = _rows(dimensions=["publisher_name"], measure="bylines")
        by = {r["Publisher name"]: r["Unique bylines"] for r in rows}
        assert by["KPLR11/Fox 2 Now"] == 3
        assert by["The Arrow"] == 6

    def test_the_measure_is_named(self, corpus):
        from visuals.corpus import run_spec

        _, meta = run_spec(
            {"dimensions": ["publisher_name"], "measure": "bylines"}, ALL_SCOPES
        )
        assert meta["measure"]["label"] == "Unique bylines"
        assert [d["key"] for d in meta["dimensions"]] == ["publisher_name"]


class TestAFilterOnBylines:
    def test_keeps_the_ticked_name_only(self, corpus):
        rows = _rows(
            dimensions=["author"],
            measure="articles",
            only={"author": ["Chris Regnier"]},
        )
        assert [(r["Byline"], r["Articles"]) for r in rows] == [("Chris Regnier", 2)]

    def test_offers_names_not_strings(self, corpus):
        from visuals.corpus import values_of

        offered = dict(values_of("author", {}, ALL_SCOPES))
        assert offered["Nick Gladney"] == 4
        assert "Nick Gladney, Chris Regnier" not in offered
        assert "" not in offered
