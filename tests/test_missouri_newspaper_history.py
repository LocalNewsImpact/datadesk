"""The Missouri newspaper history river: a one-off renderer that still
publishes, pins and caches like every other visual."""

from unittest import mock

import pytest

from visuals.models import Visual
from visuals.services import publish, refresh_snapshot

pytestmark = pytest.mark.django_db

ROWS = [
    {
        "kind": "year",
        "year": 1870,
        "publishing": 252,
        "founded": ["Linn County news (Brookfield)"],
        "closed": [],
        "merged": [],
        "absorbed": 0,
    },
    {
        "kind": "year",
        "year": 2024,
        "publishing": 220,
        "founded": [],
        "closed": ["Nodaway News Leader (Maryville)"],
        "merged": ["Monett times \u2192 Lawrence County Record"],
        "absorbed": 1,
    },
]


@pytest.fixture
def visual(django_user_model):
    author = django_user_model.objects.create_user(
        "author", email="author@localnewsimpact.org"
    )
    v = Visual.objects.create(
        slug="missouri-newspaper-history",
        title="Missouri newspapers since 1808",
        source_kind="gcs",
        bucket_path="gs://bucket/missouri_newspaper_history.json",
        template="missouri_newspaper_history",
        config={"title": "Two centuries of Missouri newspapers"},
        created_by=author,
    )
    with mock.patch("visuals.services.fetch_source_data", return_value=ROWS):
        refresh_snapshot(v, author)
    publish(v, author)
    return v


def test_the_renderer_is_a_valid_template(visual):
    visual.full_clean()


def test_the_embed_draws_the_river(client, visual):
    page = client.get("/embed/missouri-newspaper-history/")
    assert page.status_code == 200
    body = page.content.decode()
    assert "d3.min.js" in body and "d3-sankey" not in body
    assert 'id="nr-chart"' in body
    assert "Two centuries of Missouri newspapers" in body
    assert "/visuals/missouri-newspaper-history/data.json" in body


def test_the_feed_serves_the_pinned_years(client, visual):
    feed = client.get("/visuals/missouri-newspaper-history/data.json").json()
    assert feed["version"] == 1
    assert feed["data"] == ROWS


def test_a_reader_can_see_the_papers_behind_a_year_or_a_decade(client, visual):
    """Hovering or tapping a year, or a decade's bar, names the papers
    founded, merged and closed in a panel that stays in view."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert 'class="nr-panel"' in body
    assert "Hover or tap a year, or a decade" in body
    assert "dd-tip" not in body


def test_mergers_and_closures_leave_on_the_right(client, visual):
    """Gold is a paper merged into one that carried on, red one that closed;
    both leave the river at their year and gather at their decade's end."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert '"Merged"' in body and '"Closed"' in body and '"Founded"' in body
    assert "publishing today" in body


def test_no_publisher_is_printed_ahead_of_the_note(client, visual):
    note = {"note": "Missouri School of Journalism analysis"}
    Visual.objects.filter(pk=visual.pk).update(config=note)
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert "Source: Missouri School of Journalism analysis" in body
    assert "Local News Impact Consortium</a>" not in body
