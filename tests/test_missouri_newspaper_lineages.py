"""The family trees of one county's newspapers: a one-off renderer that
still publishes, pins and caches like every other visual."""

from unittest import mock

import pytest

from visuals.models import Visual
from visuals.services import publish, refresh_snapshot

pytestmark = pytest.mark.django_db

ROWS = [
    {
        "kind": "title",
        "id": 2,
        "county": "Grundy",
        "town": "Trenton",
        "title": "Republican-times",
        "start": 1945,
        "end": 1952,
        "live": False,
        "alone": False,
    },
    {
        "kind": "title",
        "id": 1,
        "county": "Grundy",
        "town": "Trenton",
        "title": "Trenton Republican-times",
        "start": 1964,
        "end": None,
        "live": True,
        "alone": False,
    },
    {"kind": "link", "source": 2, "target": 1, "how": "renamed"},
]


@pytest.fixture
def visual(django_user_model):
    author = django_user_model.objects.create_user(
        "author", email="author@localnewsimpact.org"
    )
    v = Visual.objects.create(
        slug="missouri-newspaper-lineages",
        title="Where Missouri's newspapers came from",
        source_kind="gcs",
        bucket_path="gs://bucket/missouri_newspaper_lineages.json",
        template="missouri_newspaper_lineages",
        config={"title": "Where Missouri's newspapers came from"},
        created_by=author,
    )
    with mock.patch("visuals.services.fetch_source_data", return_value=ROWS):
        refresh_snapshot(v, author)
    publish(v, author)
    return v


def test_the_renderer_is_a_valid_template(visual):
    visual.full_clean()


def test_the_embed_offers_a_county_and_a_search(client, visual):
    page = client.get("/embed/missouri-newspaper-lineages/")
    assert page.status_code == 200
    body = page.content.decode()
    assert 'class="ft-county"' in body and 'class="ft-find"' in body
    assert 'id="ft-chart"' in body
    assert "/visuals/missouri-newspaper-lineages/data.json" in body


def test_one_county_is_drawn_at_a_time_with_no_statewide_overview(client, visual):
    """The page is the county tree; the statewide line-per-paper overview,
    which could not show branching, is gone."""
    body = client.get("/embed/missouri-newspaper-lineages/").content.decode()
    assert "nl-overview" not in body
    # every paper is drawn; none is hidden behind a toggle for never having
    # been renamed or merged
    assert 'class="ft-alone"' not in body and "kept one name" not in body
    assert "no recorded predecessor" not in body
    assert "carries on along the same row" not in body
    assert "Merged" in body and "Split" in body


def test_a_default_county_comes_from_the_config(client, visual):
    Visual.objects.filter(pk=visual.pk).update(config={"county": "Boone"})
    body = client.get("/embed/missouri-newspaper-lineages/").content.decode()
    assert 'data-county="Boone"' in body


def test_the_feed_serves_the_pinned_tree(client, visual):
    feed = client.get("/visuals/missouri-newspaper-lineages/data.json").json()
    assert feed["version"] == 1
    assert feed["data"] == ROWS


def test_no_publisher_is_printed_ahead_of_the_note(client, visual):
    """The source line is the visual's own note; the consortium is not
    printed as the source by default."""
    note = {"note": "Missouri School of Journalism analysis"}
    Visual.objects.filter(pk=visual.pk).update(config=note)
    body = client.get("/embed/missouri-newspaper-lineages/").content.decode()
    assert "Source: Missouri School of Journalism analysis" in body
    assert "Local News Impact Consortium</a>" not in body
