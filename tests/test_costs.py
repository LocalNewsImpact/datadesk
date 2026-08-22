"""The cost dashboard: recorded rollups, the billed join, degraded modes."""

from datetime import UTC, datetime
from unittest import mock

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

URL = "/explorer/costs/"


@pytest.fixture
def admin(client):
    """Cost is an Admin section (accounts.sections.ADMIN_SECTIONS); the
    role refusal itself is covered in tests/test_admin_access.py."""
    user = User.objects.create_user("admin", email="admin@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="admin"))
    client.force_login(user)
    return user


@pytest.fixture
def costed_corpus(crawler_schema):
    mo = Dataset.objects.create(id="d1", slug="missouri", label="Missouri")
    tribune = Source.objects.create(
        id="s1", host="tribune.example", host_norm="tribune.example"
    )
    DatasetSource.objects.create(id="ds1", dataset=mo, source=tribune)
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=tribune)
    for i, (cost, day) in enumerate([(0.004, 2), (0.006, 2), (0.01, 3)]):
        article = Article.objects.create(
            id=f"a{i}",
            candidate_link=link,
            title=f"Story {i}",
            status="enriched",
            wire_check_status="complete",
            created_at=datetime(2026, 3, day, tzinfo=UTC),
        )
        ArticleEnrichment.objects.create(
            article=article,
            cost_usd=cost,
            model="deepseek/deepseek-v3.2",
            enriched_at=datetime(2026, 3, day, tzinfo=UTC),
        )


def test_requires_a_role(client):
    assert client.get(URL).status_code == 302


def test_recorded_rollups(client, admin, costed_corpus):
    # The billed side would probe for GCP credentials (a slow network
    # timeout on a dev machine); pin it offline.
    with mock.patch("explorer.views.billed_costs", return_value=None):
        response = client.get(URL)
    content = response.content.decode()
    assert response.status_code == 200
    assert "$0.02" in content  # total 0.004 + 0.006 + 0.01
    assert "Missouri" in content
    assert "deepseek/deepseek-v3.2" in content
    assert "BigQuery not connected" in content


def test_billed_side_joined_by_day(client, admin, costed_corpus):
    billed_rows = [
        {
            "day": datetime(2026, 3, 2, tzinfo=UTC).date(),
            "billed": 0.007,
            "cache_discount": 0.003,
            "requests": 2,
            "cached_requests": 1,
        }
    ]
    with mock.patch("explorer.analytics.query_rows", return_value=billed_rows):
        response = client.get(URL)
    content = response.content.decode()
    assert "Cache discount" in content
    assert "$0.01" in content or "0.007" in content or "$0.00" in content
    assert "0.50 hit rate" in content


def test_degrades_with_neither_source(client, admin):
    with mock.patch("explorer.views.billed_costs", return_value=None):
        response = client.get(URL)
    assert response.status_code == 200
    assert "Neither cost source is connected" in response.content.decode()
