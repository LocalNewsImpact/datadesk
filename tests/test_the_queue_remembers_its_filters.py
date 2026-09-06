"""The extraction queue keeps the filters it was left on.

A reviewer working one dataset, one window and one case left the page to
look an article up and came back to the unfiltered queue, having to
choose all three again -- and a queue whose filters reset reads as a
queue that lost the work.

Clearing has to stay possible, so the two are told apart by how they
arrive: "Clear all" and un-toggling the last facet are htmx requests,
and a full page load carrying no filters is arriving from elsewhere in
the console.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from review.views import QUEUE_FILTERS

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/review/queue/"


@pytest.fixture
def reviewer(client):
    user = User.objects.create_user("rev", email="rev@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="reviewer")
    client.force_login(user)
    return user


@pytest.fixture
def corpus(crawler_schema):
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    link = CandidateLink.objects.create(
        id="cl1", source_id=source.id, url="https://a.example/one"
    )
    return Article.objects.create(
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


def test_choosing_filters_records_them(client, reviewer, corpus):
    client.get(URL, {"dataset": "mo", "days": "90", "case": "paywall_stub"})
    assert client.session[QUEUE_FILTERS] == {
        "dataset": "mo",
        "days": "90",
        "case": "paywall_stub",
    }


def test_coming_back_returns_to_them(client, reviewer, corpus):
    client.get(URL, {"dataset": "mo", "days": "90"})
    response = client.get(URL)
    assert response.status_code == 302
    assert "dataset=mo" in response["Location"]
    assert "days=90" in response["Location"]


def test_the_page_is_not_a_redirect_loop(client, reviewer, corpus):
    """The restored URL carries the filters, so it renders."""
    client.get(URL, {"dataset": "mo"})
    assert client.get(URL, follow=True).status_code == 200


def test_the_page_number_is_not_remembered(client, reviewer, corpus):
    """Page seven of a queue that has been worked since is not where
    anybody was."""
    client.get(URL, {"dataset": "mo", "page": "7"})
    assert "page" not in client.session[QUEUE_FILTERS]


def test_clearing_the_filters_clears_the_memory(client, reviewer, corpus):
    client.get(URL, {"dataset": "mo", "days": "90"})
    client.get(URL, HTTP_HX_REQUEST="true")
    assert QUEUE_FILTERS not in client.session
    assert client.get(URL).status_code == 200


def test_nothing_remembered_renders_the_queue(client, reviewer, corpus):
    assert client.get(URL).status_code == 200


def test_a_new_choice_replaces_the_old_one(client, reviewer, corpus):
    client.get(URL, {"dataset": "mo", "days": "90"})
    client.get(URL, {"days": "365"})
    assert client.session[QUEUE_FILTERS] == {"days": "365"}
