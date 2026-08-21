"""The enrichment grid: geography filters, confidence bands, access."""

from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import Group, User

from explorer.models import (
    Article,
    ArticleEnrichment,
    CandidateLink,
    Dataset,
    DatasetSource,
    Source,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/explorer/enrichment/"


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="viewer"))
    client.force_login(user)
    return user


@pytest.fixture
def enriched_corpus(crawler_schema):
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

    def make(i, link, **enrichment):
        article = Article.objects.create(
            id=f"a{i}",
            candidate_link=link,
            title=f"Story {i}",
            status="enriched",
            wire_check_status="complete",
            created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        return ArticleEnrichment.objects.create(article=article, **enrichment)

    make(
        1,
        cl1,
        scope="city_municipality",
        scope_confidence=0.95,
        point_place="Columbia",
        point_geoid="2915670",
        point_geoid_level="place",
        enriched_at=datetime(2026, 3, 2, tzinfo=UTC),
    )
    make(
        2,
        cl1,
        scope="statewide",
        scope_confidence=0.5,
        geo_skip_reason="publication_state_unknown",
        enriched_at=datetime(2026, 3, 3, tzinfo=UTC),
    )
    make(
        3,
        cl2,
        scope="city_municipality",
        scope_confidence=0.8,
        point_place="Allentown",
        point_geoid="4202000",
        point_geoid_level="place",
        skip_reason="short_content",
        enriched_at=datetime(2026, 3, 4, tzinfo=UTC),
    )


def _stories(response):
    content = response.content.decode()
    return [f"Story {i}" for i in range(1, 4) if f"Story {i}" in content]


def test_requires_a_role(client):
    assert client.get(URL).status_code == 302
    client.force_login(User.objects.create_user("norole", email="n@example.org"))
    assert client.get(URL).status_code == 403


def test_lists_records_newest_first(client, viewer, enriched_corpus):
    response = client.get(URL)
    assert response.status_code == 200
    assert _stories(response) == ["Story 1", "Story 2", "Story 3"]
    assert "2915670" in response.content.decode()


def test_scope_filter(client, viewer, enriched_corpus):
    assert _stories(client.get(URL, {"scope": "statewide"})) == ["Story 2"]


def test_fips_prefix_filter(client, viewer, enriched_corpus):
    assert _stories(client.get(URL, {"fips": "29"})) == ["Story 1"]
    assert _stories(client.get(URL, {"fips": "42"})) == ["Story 3"]


def test_skip_reason_filters(client, viewer, enriched_corpus):
    assert _stories(client.get(URL, {"skip": "short_content"})) == ["Story 3"]
    assert _stories(client.get(URL, {"geo_skip": "publication_state_unknown"})) == [
        "Story 2"
    ]


def test_no_point_filter(client, viewer, enriched_corpus):
    assert _stories(client.get(URL, {"no_point": "1"})) == ["Story 2"]


def test_confidence_band(client, viewer, enriched_corpus):
    assert _stories(client.get(URL, {"conf_max": "0.6"})) == ["Story 2"]
    assert _stories(client.get(URL, {"conf_min": "0.9"})) == ["Story 1"]


def test_dataset_filter(client, viewer, enriched_corpus):
    assert _stories(client.get(URL, {"dataset": "lehigh"})) == ["Story 3"]


def test_htmx_fragment(client, viewer, enriched_corpus):
    content = client.get(URL, HTTP_HX_REQUEST="true").content.decode()
    assert "Story 1" in content
    assert "<html" not in content


def test_degrades_without_crawler_tables(client, viewer):
    response = client.get(URL)
    assert response.status_code == 200
    assert "not connected" in response.content.decode()
