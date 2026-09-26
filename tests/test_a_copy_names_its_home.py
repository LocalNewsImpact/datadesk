"""Each source newsroom, who republished its work, and how often.

The byline review records the newsroom whose reporting a republished copy is
(`articles.syndicated_from_source_id`). Nothing in the builder could read it,
so the republishing table -- the Missouri Independent, then the Sedalia
Democrat with its stories -- could not be drawn, nor narrowed to one source.
"""

import datetime as dt

import pytest

from accounts.access import ALL_SCOPES
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def syndication(crawler_schema):
    dataset = Dataset.objects.create(id="d1", slug="mizzou", label="Missouri")
    names = {
        "mi": "Missouri Independent",
        "sedalia": "Sedalia Democrat",
        "fulton": "Fulton Sun",
        "kc": "The Kansas City Star",
    }
    sources = {}
    for i, (key, name) in enumerate(names.items()):
        sources[key] = Source.objects.create(
            id=f"s-{key}",
            host=f"{key}.example",
            host_norm=f"{key}.example",
            canonical_name=name,
        )
        DatasetSource.objects.create(id=f"ds{i}", dataset=dataset, source=sources[key])
    stories = [
        # (carried by, credited to, status)
        ("sedalia", "mi", "wire"),
        ("sedalia", "mi", "wire"),
        ("sedalia", "mi", "wire"),
        ("fulton", "mi", "wire"),
        ("sedalia", "kc", "wire"),
        ("fulton", None, "wire"),  # uncredited wire: no home to name
        ("mi", None, "enriched"),  # the home newsroom's own story
    ]
    for i, (carrier, home, status) in enumerate(stories):
        link = CandidateLink.objects.create(
            id=f"cl{i}",
            url=f"https://{carrier}.example/{i}",
            source=sources[carrier],
            dataset_id="d1",
        )
        Article.objects.create(
            id=f"a{i}",
            candidate_link=link,
            dataset_id="d1",
            url=link.url,
            author="Rudi Keller",
            status=status,
            syndicated_from_source_id=sources[home].id if home else None,
            publish_date=dt.datetime(2026, 3, 10, tzinfo=dt.UTC),
            created_at=dt.datetime(2026, 3, 10, tzinfo=dt.UTC),
        )
    return dataset


def _table(**extra):
    from visuals.corpus import run_values

    spec = {
        "dimensions": ["syndication_source", "republisher"],
        "measures": ["articles"],
        "subset": "complete",
        **extra,
    }
    rows, _ = run_values(spec, ALL_SCOPES)
    return {(r["Source"], r["Republisher"]): r["Articles"] for r in rows}


def test_each_source_and_who_republished_it(syndication):
    assert _table() == {
        ("Missouri Independent", "Sedalia Democrat"): 3,
        ("Missouri Independent", "Fulton Sun"): 1,
        ("The Kansas City Star", "Sedalia Democrat"): 1,
    }


def test_one_source_and_everybody_who_ran_it(syndication):
    assert _table(only={"syndication_source": ["Missouri Independent"]}) == {
        ("Missouri Independent", "Sedalia Democrat"): 3,
        ("Missouri Independent", "Fulton Sun"): 1,
    }


def test_republishers_alone_under_one_source(syndication):
    from visuals.corpus import run_values

    rows, _ = run_values(
        {
            "dimensions": ["republisher"],
            "measures": ["articles"],
            "subset": "complete",
            "only": {"syndication_source": ["Missouri Independent"]},
        },
        ALL_SCOPES,
    )
    assert {r["Republisher"]: r["Articles"] for r in rows} == {
        "Sedalia Democrat": 3,
        "Fulton Sun": 1,
    }


def test_a_copy_without_a_source_is_not_a_blank_one(syndication):
    sources = {source for source, _ in _table()}
    assert None not in sources and "" not in sources


def test_a_newsrooms_own_stories_are_not_republishing(syndication):
    assert all(carrier != source for source, carrier in _table())


def test_both_sit_with_the_publisher_fields():
    from visuals.corpus import GROUP_OF

    assert GROUP_OF["syndication_source"] == GROUP_OF["publisher_name"]
    assert GROUP_OF["republisher"] == GROUP_OF["publisher_name"]


def test_neither_is_called_home_or_credited():
    from visuals.corpus import DIMENSIONS

    for key in ("syndication_source", "republisher"):
        words = (DIMENSIONS[key]["label"] + DIMENSIONS[key]["note"]).lower()
        assert "home" not in words and "credit" not in words
