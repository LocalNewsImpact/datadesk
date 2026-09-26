"""Each home newsroom, who carried its work, and how often.

The byline review credits a wire copy to the newsroom whose reporting it is
(`articles.syndicated_from_source_id`). Nothing in the builder could read it,
so the syndication table -- the Missouri Independent, then the Sedalia
Democrat with eight of its stories -- could not be drawn.
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
        "dimensions": ["home_newsroom", "publisher_name"],
        "measures": ["articles"],
        "subset": "complete",
        **extra,
    }
    rows, _ = run_values(spec, ALL_SCOPES)
    return {(r["Home newsroom"], r["Publisher name"]): r["Articles"] for r in rows}


def test_each_home_newsroom_and_who_carried_it(syndication):
    assert _table() == {
        ("Missouri Independent", "Sedalia Democrat"): 3,
        ("Missouri Independent", "Fulton Sun"): 1,
        ("The Kansas City Star", "Sedalia Democrat"): 1,
    }


def test_an_uncredited_copy_is_not_a_blank_home(syndication):
    homes = {home for home, _ in _table()}
    assert None not in homes and "" not in homes


def test_the_home_newsrooms_own_stories_are_not_syndication(syndication):
    assert all(carrier != home for home, carrier in _table())


def test_it_is_offered_beside_the_publisher(syndication):
    from visuals.corpus import GROUP_OF

    assert GROUP_OF["home_newsroom"] == GROUP_OF["publisher_name"]
