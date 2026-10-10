"""The family tree of Missouri's newspapers still publishing: a one-off
renderer that still publishes, pins and caches like every other visual."""

from unittest import mock

import pytest

from visuals.models import Visual
from visuals.services import publish, refresh_snapshot

pytestmark = pytest.mark.django_db

ROWS = [
    {
        "paper": 1,
        "paper_title": "Trenton Republican-times",
        "county": "Grundy",
        "town_now": "Trenton",
        "source": 2,
        "source_label": "Republican-times",
        "source_town": "Trenton",
        "source_start": 1945,
        "source_end": 1952,
        "source_role": "renamed",
        "target": 1,
        "target_label": "Trenton Republican-times",
        "target_town": "Trenton",
        "target_start": 1964,
        "target_end": None,
        "target_role": "current",
        "value": 1,
    }
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
    assert 'class="nl-county"' in body and 'class="nl-find"' in body
    assert 'id="nl-overview"' in body and 'id="nl-tree"' in body
    assert "/visuals/missouri-newspaper-lineages/data.json" in body


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
