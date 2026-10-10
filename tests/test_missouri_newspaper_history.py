"""The Missouri newspaper history sankey: a one-off renderer that still
publishes, pins and caches like every other visual."""

from unittest import mock

import pytest

from visuals.models import Visual
from visuals.services import publish, refresh_snapshot

pytestmark = pytest.mark.django_db

ROWS = [
    {
        "source": "P1870",
        "source_label": "Publishing in 1870",
        "source_kind": "publishing",
        "source_col": 2,
        "target": "P1895",
        "target_label": "Publishing in 1895",
        "target_kind": "publishing",
        "target_col": 3,
        "kind": "publishing",
        "value": 120,
    },
    {
        "source": "P1870",
        "source_label": "Publishing in 1870",
        "source_kind": "publishing",
        "source_col": 2,
        "target": "C1895",
        "target_label": "Closed 1871–1895",
        "target_kind": "closed",
        "target_col": 3,
        "kind": "closed",
        "value": 134,
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


def test_the_embed_draws_with_the_sankey_runtime(client, visual):
    page = client.get("/embed/missouri-newspaper-history/")
    assert page.status_code == 200
    body = page.content.decode()
    assert "d3-sankey.min.js" in body
    assert "Two centuries of Missouri newspapers" in body
    assert "/visuals/missouri-newspaper-history/data.json" in body


def test_the_feed_serves_the_pinned_flows(client, visual):
    feed = client.get("/visuals/missouri-newspaper-history/data.json").json()
    assert feed["version"] == 1
    assert feed["data"] == ROWS


def test_a_merged_paper_joins_the_survivor_and_is_not_counted_twice(client, visual):
    """A merger is drawn into the paper that absorbed it, so the publishing
    block's count leaves the merged-in papers out and names them beside it."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert "Merged into a surviving paper" in body
    assert "merged in" in body
    assert "Still publishing at the next checkpoint" in body


def test_a_reader_can_follow_papers_to_where_they_ended_up(client, visual):
    """The feed may carry each newspaper's path; the embed offers to follow a
    selection and no longer only repeats the printed numbers on hover."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert "follow those newspapers forward to where they ended up" in body
    assert "Try: papers founded 1876" in body
    assert 'class="nh-follow"' in body
    assert "dd-tip" not in body


def test_no_publisher_is_printed_ahead_of_the_note(client, visual):
    note = {"note": "Missouri School of Journalism analysis"}
    Visual.objects.filter(pk=visual.pk).update(config=note)
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert "Source: Missouri School of Journalism analysis" in body
    assert "Local News Impact Consortium</a>" not in body


def test_the_papers_that_survive_are_named(client, visual):
    """A selection lists the papers still publishing, not only their count."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert "Publishing today" in body and "nh-living" in body


def test_custom_era_notes_reach_the_page(client, visual):
    """config.annotations (keyed by a span's end year) replaces that span's
    generated note."""
    notes = {"annotations": {"1905": "The peak."}}
    Visual.objects.filter(pk=visual.pk).update(config=notes)
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert 'id="nh-notes"' in body and "The peak." in body
