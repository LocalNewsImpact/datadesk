"""The articles grid: filters, pagination, access, degraded mode.

Fixture data goes through the unmanaged ORM models — in tests the crawler
alias is writable sqlite, which also exercises CrawlerRouter's routing;
in production the same write would be refused by Postgres (datadesk_ro).
"""

from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import Group, User

from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/explorer/articles/"


def _article(i, source_link, **overrides):
    fields = {
        "id": f"a{i}",
        "candidate_link": source_link,
        "url": f"https://example.org/{i}",
        "title": f"Story {i}",
        "status": "labeled",
        "wire_check_status": "complete",
        "created_at": datetime(2026, 3, 1, tzinfo=UTC),
        "publish_date": datetime(2026, 3, 1, tzinfo=UTC),
        "primary_label": "news",
        "primary_label_confidence": 0.9,
    }
    fields.update(overrides)
    return Article.objects.create(**fields)


@pytest.fixture
def corpus(crawler_schema):
    """Two datasets, two publishers, four articles with contrasts."""
    mo = Dataset.objects.create(id="d1", slug="missouri", label="Missouri")
    lv = Dataset.objects.create(id="d2", slug="lehigh", label="Lehigh Valley")
    tribune = Source.objects.create(
        id="s1", host="tribune.example", host_norm="tribune.example"
    )
    herald = Source.objects.create(
        id="s2", host="herald.example", host_norm="herald.example"
    )
    DatasetSource.objects.create(id="ds1", dataset=mo, source=tribune)
    DatasetSource.objects.create(id="ds2", dataset=lv, source=herald)
    cl1 = CandidateLink.objects.create(id="cl1", url="https://t/", source=tribune)
    cl2 = CandidateLink.objects.create(id="cl2", url="https://h/", source=herald)

    _article(1, cl1)
    _article(
        2,
        cl1,
        status="extracted",
        wire_check_status="pending",
        primary_label_confidence=0.4,
        publish_date=datetime(2026, 1, 15, tzinfo=UTC),
    )
    _article(3, cl2, primary_label="sports")
    _article(
        4,
        cl2,
        title="Council votes on budget",
        primary_label=None,
        primary_label_confidence=None,
    )


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="viewer"))
    client.force_login(user)
    return user


def _titles(response):
    content = response.content.decode()
    return [f"Story {i}" for i in range(1, 5) if f"Story {i}" in content]


def test_anonymous_is_sent_to_sign_in(client):
    response = client.get(URL)
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


def test_no_role_is_forbidden(client):
    client.force_login(User.objects.create_user("norole", email="n@example.org"))
    assert client.get(URL).status_code == 403


def test_grid_lists_the_corpus(client, viewer, corpus):
    response = client.get(URL)
    assert response.status_code == 200
    assert _titles(response) == ["Story 1", "Story 2", "Story 3"]
    assert "Council votes on budget" in response.content.decode()


def test_dataset_filter_follows_membership(client, viewer, corpus):
    response = client.get(URL, {"dataset": "missouri"})
    assert _titles(response) == ["Story 1", "Story 2"]


def test_status_and_wire_filters(client, viewer, corpus):
    assert _titles(client.get(URL, {"status": "extracted"})) == ["Story 2"]
    assert _titles(client.get(URL, {"wire": "pending"})) == ["Story 2"]


def test_publisher_search(client, viewer, corpus):
    response = client.get(URL, {"publisher": "Herald"})
    assert _titles(response) == ["Story 3"]
    assert "Council votes on budget" in response.content.decode()


def test_label_and_confidence_filters(client, viewer, corpus):
    assert _titles(client.get(URL, {"label": "sports"})) == ["Story 3"]
    assert _titles(client.get(URL, {"conf_max": "0.5"})) == ["Story 2"]
    assert _titles(client.get(URL, {"conf_min": "0.5", "label": "news"})) == ["Story 1"]


def test_date_range_filter(client, viewer, corpus):
    response = client.get(URL, {"from": "2026-01-01", "to": "2026-01-31"})
    assert _titles(response) == ["Story 2"]


def test_title_search(client, viewer, corpus):
    response = client.get(URL, {"q": "council"})
    assert _titles(response) == []
    assert "Council votes on budget" in response.content.decode()


def test_filter_vocab_comes_from_the_data(client, viewer, corpus):
    content = client.get(URL).content.decode()
    for value in ("labeled", "extracted", "pending", "complete", "sports"):
        assert value in content
    assert "Missouri" in content and "Lehigh Valley" in content


def test_pagination(client, viewer, crawler_schema):
    source = Source.objects.create(id="s1", host="t.example", host_norm="t.example")
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=source)
    for i in range(60):
        _article(i, link)
    first = client.get(URL)
    assert "page 1 of 2" in first.content.decode()
    second = client.get(URL, {"page": "2"})
    assert "page 2 of 2" in second.content.decode()


def test_htmx_request_gets_only_the_results_fragment(client, viewer, corpus):
    response = client.get(URL, HTTP_HX_REQUEST="true")
    content = response.content.decode()
    assert "Story 1" in content
    assert "<html" not in content
    assert "filter-bar" not in content


def test_degrades_without_crawler_tables(client, viewer):
    response = client.get(URL)
    assert response.status_code == 200
    assert "not connected" in response.content.decode()
