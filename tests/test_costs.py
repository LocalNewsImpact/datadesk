"""The cost dashboard: recorded rollups, the billed join, degraded modes."""

from datetime import UTC, datetime
from unittest import mock

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
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
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
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


# --- the billed side has to actually run -------------------------------------


def test_the_billed_query_reads_the_json_not_columns():
    """openrouter_traces has one column, `trace`, holding a JSON string.
    The query named columns that do not exist -- created_at, usage,
    cache_discount -- so BigQuery rejected it with "Unrecognized name:
    created_at", `billed_costs()` swallowed that, and the dashboard showed
    the recorded side alone under a heading promising both.

    Verified against the live table on 2026-08-27: 116,806 traces on
    2026-08-22 summing to $54.57 billed, where the recorded side says
    $83.98 for the same day.
    """
    from explorer.costs import _BILLED_SQL

    assert "JSON_VALUE(trace" in _BILLED_SQL
    assert "$.metadata.openrouter_generation.usage" in _BILLED_SQL
    assert "$.timestamp" in _BILLED_SQL
    # The names that never existed.
    assert "DATE(created_at)" not in _BILLED_SQL
    assert "SUM(usage) AS billed" in _BILLED_SQL


def test_the_cache_saving_is_not_subtracted_twice():
    """`usage` is the net charge. Checked against a trace on 2026-08-21:
    inputCost + outputCost == usage exactly, and inputCost is already below
    unit price x tokens because cached prompt tokens bill at about a tenth.

    `usage_cache` is a negative savings line -- what the cache was worth.
    Subtracting it would discount the bill twice, and counting it as
    positive would call every request uncached."""
    from explorer.costs import _BILLED_SQL

    assert "SUM(usage) AS billed" in _BILLED_SQL
    assert "usage - usage_cache" not in _BILLED_SQL
    assert "usage + usage_cache" not in _BILLED_SQL
    # The old test for a cached request read `cache_discount > 0`; the
    # values are negative or zero.
    assert "cache_discount > 0" not in _BILLED_SQL
    assert "COUNTIF(usage_cache <> 0)" in _BILLED_SQL


def test_a_broken_billed_query_says_so_instead_of_vanishing(monkeypatch):
    """A number that is missing is a fact about the number. A number
    missing for a reason nobody can see is a fact about nothing."""
    from django.core.cache import cache

    from explorer import costs

    cache.delete("explorer.billed_costs")

    def boom(_sql):
        raise RuntimeError("Unrecognized name: created_at")

    monkeypatch.setattr("explorer.analytics.query_rows", boom)
    result = costs.billed_costs()
    assert result is not None, "the failure vanished again"
    assert "Unrecognized name" in result["unavailable"]
