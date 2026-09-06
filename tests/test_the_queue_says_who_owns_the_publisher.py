"""The extraction queue's publisher header names the owner.

A run of flagged stories under one publisher is often one chain's house
style rather than one newsroom's -- a shared CMS producing the same bad
capture across forty titles. The owner is what makes that visible, and
the reviewer had to open a record to see it.

Said in the same words and the same style as the paywalls page, which
already shows it: an uppercase `Owner` label and the name beside it.
"""

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/review/queue/"


@pytest.fixture
def reviewer(client):
    user = User.objects.create_user("rev", email="rev@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="reviewer")
    client.force_login(user)
    return user


def _publisher(crawler_schema, owner):
    """One flagged article under a publisher with (or without) an owner."""
    from datetime import timedelta

    from django.utils import timezone

    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(
        id="s1",
        host="a.example",
        host_norm="a.example",
        canonical_name="The Example Herald",
        owner=owner,
    )
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    link = CandidateLink.objects.create(
        id="cl1", source_id=source.id, url="https://a.example/one"
    )
    Article.objects.create(
        id="a1",
        candidate_link=link,
        title="A flagged story",
        status="not_article",
        wire_check_status="complete",
        content="A captured body.",
        text="A captured body.",
        publish_date=timezone.now() - timedelta(days=2),
        created_at=timezone.now() - timedelta(days=2),
        enrichment_attempts=0,
    )
    return source


def test_the_owner_is_named_in_the_publisher_header(client, reviewer, crawler_schema):
    _publisher(crawler_schema, owner="Lee Enterprises")
    content = client.get(URL, {"all": "1"}).content.decode()
    assert "Lee Enterprises" in content
    assert 'class="owner"' in content
    assert ">Owner<" in content


def test_a_publisher_with_no_owner_gets_no_label(client, reviewer, crawler_schema):
    """254 of 1,149 sources carry one, so the rest must not show a
    labelled blank."""
    _publisher(crawler_schema, owner=None)
    content = client.get(URL, {"all": "1"}).content.decode()
    assert 'class="owner"' not in content
    assert ">Owner<" not in content


def test_an_empty_owner_is_the_same_as_none(client, reviewer, crawler_schema):
    _publisher(crawler_schema, owner="")
    content = client.get(URL, {"all": "1"}).content.decode()
    assert ">Owner<" not in content


def test_the_owner_is_styled_where_the_queue_puts_it():
    """The paywalls page defines the look; the queue header reuses it
    rather than growing a second one that can drift."""
    css = pytest.importorskip("pathlib").Path("static/css/datadesk.css").read_text()
    assert ".rec-head .goes-to" in css
    assert ".rec-head .owner" in css
    # The paywalls column keeps its own gutter, which the header must not
    # inherit -- the label sits inline after the publisher's name.
    assert ".publisher .goes-to { flex: 0 0 3.4rem; }" in css
