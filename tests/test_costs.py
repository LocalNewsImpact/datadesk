"""The cost dashboard: recorded rollups, the billed join, degraded modes."""

import re
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

    def boom(_sql, **_params):
        raise RuntimeError("Unrecognized name: created_at")

    monkeypatch.setattr("explorer.analytics.query_rows", boom)
    result = costs.billed_costs()
    assert result is not None, "the failure vanished again"
    assert "Unrecognized name" in result["unavailable"]


# --- what Google charges -----------------------------------------------------


def test_gcp_costs_say_why_when_there_is_no_export(monkeypatch):
    """Billing export is enabled per billing account in the console --
    there is no gcloud command for it -- and it does not backfill. Until
    it is on, the table does not exist, and the page has to say that
    rather than show nothing."""
    from django.core.cache import cache

    from explorer import costs

    cache.delete("explorer.gcp_costs")

    def boom(_sql, **_params):
        raise RuntimeError("Not found: Table gcp_billing_export_v1_*")

    monkeypatch.setattr("explorer.analytics.query_rows", boom)
    result = costs.gcp_costs()
    assert "Not found" in result["unavailable"]


def test_a_labelled_job_is_attributed_and_the_rest_is_not(monkeypatch):
    """A worker job runs for one dataset, so its cost is attributed. One
    database and one console serve every dataset at once, so that half
    cannot be measured and must be marked as an estimate."""
    from django.core.cache import cache

    from explorer import costs

    cache.delete("explorer.gcp_costs")
    rows = [
        {
            "month": "2026-08",
            "project": "p",
            "service": "Cloud Run",
            "dataset": "mizzou",
            "cost": 10.0,
            "credits": 0.0,
        },
        {
            "month": "2026-08",
            "project": "p",
            "service": "Cloud SQL",
            "dataset": None,
            "cost": 40.0,
            "credits": 0.0,
        },
    ]
    monkeypatch.setattr("explorer.analytics.query_rows", lambda _sql, **_p: rows)
    result = costs.gcp_costs()
    assert result["attributed"] == 10.0
    assert result["infrastructure"] == 40.0
    august = result["by_month"][0]
    assert august["attributed"] == 10.0
    assert august["infrastructure"] == 40.0, "the column repeated the total"
    # ...and the attributed part names the dataset it belongs to.
    assert result["by_dataset"] == [{"dataset": "mizzou", "cost": 10.0}]


def test_infrastructure_is_a_bucket_not_a_number_to_divide():
    """A load balancer, the database and the console serve every dataset at
    once and belong to none. Splitting one four ways produces four figures
    that are each wrong and together look like an answer."""
    from pathlib import Path

    from explorer import costs

    assert not hasattr(costs, "apportion"), "back to dividing up the shared half"
    source = (Path(__file__).resolve().parent.parent / "explorer/costs.py").read_text()
    assert "INFRASTRUCTURE" in source, "the bucket is not named in the reasoning"
    # The page reads these two names; renaming one without the other is how
    # a bucket quietly becomes a blank column.
    assert '"infrastructure"' in source and '"attributed"' in source


# --- the query that ships is the query that was checked ----------------------


def test_no_alias_collides_with_a_bigquery_keyword():
    """`AS at` cost a deploy: AT is reserved -- AT TIME ZONE -- and the
    billed side came back "400 Syntax error: Unexpected keyword AT".

    The query had been run against BigQuery and passed. What was run was
    an inline version written by hand in a terminal; what shipped was a
    CTE rewritten afterwards and never re-run. Checking a query that
    resembles the one you ship is not checking the one you ship.
    """
    from explorer.costs import _BILLED_SQL, _GCP_SQL

    # Not the whole reserved list -- the ones a cost query reaches for.
    reserved = {
        "at",
        "all",
        "and",
        "any",
        "array",
        "as",
        "asc",
        "by",
        "case",
        "cast",
        "current",
        "default",
        "desc",
        "distinct",
        "else",
        "end",
        "exists",
        "false",
        "for",
        "from",
        "full",
        "group",
        "hash",
        "having",
        "if",
        "in",
        "inner",
        "interval",
        "into",
        "is",
        "join",
        "left",
        "like",
        "limit",
        "natural",
        "new",
        "no",
        "not",
        "null",
        "of",
        "on",
        "or",
        "order",
        "outer",
        "over",
        "range",
        "right",
        "rows",
        "select",
        "set",
        "some",
        "struct",
        "then",
        "to",
        "true",
        "union",
        "using",
        "when",
        "where",
        "window",
        "with",
        "within",
    }
    for name, sql in (("billed", _BILLED_SQL), ("gcp", _GCP_SQL)):
        aliases = re.findall(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)", sql)
        clashes = sorted({a for a in aliases if a.lower() in reserved})
        assert clashes == [], f"{name} aliases a reserved word: {clashes}"


def test_a_missing_export_reads_as_waiting_not_as_denied(monkeypatch):
    """BigQuery answers a missing wildcard table with "Access Denied ...
    or perhaps it does not exist", because saying which would tell a
    stranger what exists. Repeating the alarming half as news sends
    somebody to check permissions that are fine."""
    from django.core.cache import cache

    from explorer import costs

    cache.delete("explorer.gcp_costs")

    def not_there(_sql, **_params):
        raise RuntimeError(
            "403 Access Denied: Table "
            "mizzou-news-crawler:billing_export.gcp_billing_export_v1_*: "
            "User does not have permission to query table, or perhaps it "
            "does not exist."
        )

    monkeypatch.setattr("explorer.analytics.query_rows", not_there)
    assert costs.gcp_costs()["waiting"] is True

    # A real permission problem on something else is not waiting.
    cache.delete("explorer.gcp_costs")
    monkeypatch.setattr(
        "explorer.analytics.query_rows",
        lambda _sql, **_p: (_ for _ in ()).throw(
            RuntimeError("403 Access Denied: no key")
        ),
    )
    assert costs.gcp_costs()["waiting"] is False


def test_only_this_application_s_projects_are_counted(monkeypatch, settings):
    """The billing account carries projects that are not this app. The
    first export had an unrelated Slack tool and a sandbox in it, and a
    total that quietly included them would answer "what does the app cost"
    with somebody else's spending."""
    from django.core.cache import cache

    from explorer import costs

    settings.GCP_COST_PROJECTS = ["lnic-datadesk", "mizzou-news-crawler"]
    cache.delete("explorer.gcp_costs:" + ",".join(sorted(settings.GCP_COST_PROJECTS)))
    seen = {}

    def capture(sql, **params):
        seen.update(params)
        seen["sql"] = sql
        return []

    monkeypatch.setattr("explorer.analytics.query_rows", capture)
    costs.gcp_costs()

    assert seen["projects"] == "lnic-datadesk,mizzou-news-crawler"
    # Split in SQL from a scalar: the runner takes scalars, and a list is
    # not a thing to paste into a query.
    assert "SPLIT(@projects" in seen["sql"]
    assert "project.id IN UNNEST" in seen["sql"]


def test_changing_the_projects_does_not_reuse_the_old_answer(monkeypatch, settings):
    """Which projects count is part of the question, so it has to be part
    of the name the answer is filed under."""
    from explorer import costs

    calls = []
    monkeypatch.setattr(
        "explorer.analytics.query_rows",
        lambda _sql, **p: calls.append(p["projects"]) or [],
    )
    settings.GCP_COST_PROJECTS = ["one"]
    costs.gcp_costs()
    settings.GCP_COST_PROJECTS = ["two"]
    costs.gcp_costs()
    assert calls == ["one", "two"], "the second answer came from the first's cache"
