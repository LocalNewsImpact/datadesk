"""Warming the caches behind the dashboard and the costs page.

Every expensive read is cached, but a cache only helps the second reader
— the first pays the full cost, measured between ten and twenty-seven
seconds depending on how much of the corpus Postgres happens to be
holding, and 110 seconds for the BigQuery one. Warming moves that cost
off whoever arrives first and onto a deploy or a schedule.
"""

import pytest
from django.core.cache import cache
from django.core.management import call_command

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

KEYS = (
    "explorer.dataset_row_counts",
    "explorer.recorded_costs",
    "explorer.corpus_summary",
    "explorer.billed_costs",
)


@pytest.fixture(autouse=True)
def billing_available(monkeypatch):
    """Stand in for BigQuery.

    `billed_costs` reads `openrouter_traces`, which no test has
    credentials for. Left alone it raises, is swallowed, and returns
    None — so every test here would see a warm run that failed, and the
    ones about reporting would be testing the wrong thing.
    """
    import explorer.analytics

    monkeypatch.setattr(
        explorer.analytics,
        "query_rows",
        lambda _sql: [
            {
                "day": "2026-08-23",
                "billed": 1.5,
                "cache_discount": 0.25,
                "requests": 40,
                "cached_requests": 10,
            }
        ],
    )


def _run(**kwargs):
    from io import StringIO

    out, err = StringIO(), StringIO()
    call_command("warm_caches", stdout=out, stderr=err, **kwargs)
    return out.getvalue(), err.getvalue()


def test_it_fills_every_dashboard_entry(crawler_schema):
    for key in KEYS:
        cache.delete(key)
    _run()
    for key in KEYS:
        assert cache.get(key) is not None, key


def test_it_reports_what_it_warmed(crawler_schema):
    out, _ = _run()
    for label in (
        "dataset row counts",
        "recorded costs",
        "corpus summary",
        "billed costs",
    ):
        assert label in out
    assert "caches warm" in out


def test_the_costs_page_is_warmed_too(crawler_schema):
    """The bug this catches: `explorer.billed_costs` was the one cache on
    /explorer/costs/ that nothing warmed, while the three beside it were
    refreshed every schedule. It is also the slowest — BigQuery rather
    than Postgres, 110 seconds uncached — so the page charged it to
    whoever arrived first after an entry expired."""
    cache.delete("explorer.billed_costs")
    _run()
    assert cache.get("explorer.billed_costs") is not None


def test_bigquery_being_unreachable_is_reported(monkeypatch, crawler_schema):
    """`billed_costs` catches every exception and returns None so the
    costs page renders its recorded half. A warm run must not read that
    silence as success."""
    import explorer.analytics

    def boom(_sql):
        raise RuntimeError("bigquery unreachable")

    monkeypatch.setattr(explorer.analytics, "query_rows", boom)
    cache.delete("explorer.billed_costs")
    out, err = _run()
    assert "returned nothing to cache" in err
    assert "could not be warmed" in out


def test_force_recomputes_a_warm_entry(crawler_schema):
    cache.set("explorer.corpus_summary", {"sentinel": True}, 3600)
    _run(force=True)
    assert cache.get("explorer.corpus_summary") != {"sentinel": True}


def test_without_force_a_warm_entry_is_left_alone(crawler_schema):
    cache.set("explorer.corpus_summary", {"sentinel": True}, 3600)
    _run()
    assert cache.get("explorer.corpus_summary") == {"sentinel": True}


def test_a_failure_is_reported_and_not_raised(monkeypatch):
    """It runs from the deploy. A cold cache is slow, not broken, and a
    deploy must not fail because the crawler database blinked."""
    import explorer.dashboard

    def boom():
        raise RuntimeError("crawler unreachable")

    monkeypatch.setattr(explorer.dashboard, "corpus_summary", boom)
    out, err = _run()
    assert "crawler unreachable" in err
    assert "could not be warmed" in out


def test_a_read_that_caches_nothing_is_a_failure(monkeypatch, crawler_schema):
    """The bug this catches: every dashboard read swallows a
    DatabaseError and returns None so the page degrades to one banner.
    A warm run against a job with no crawler credentials therefore
    "succeeded" for every entry while the cache table stayed empty."""
    import explorer.crawler

    monkeypatch.setattr(explorer.crawler, "dataset_row_counts", lambda: None)
    cache.delete("explorer.dataset_row_counts")
    out, err = _run()
    assert "returned nothing to cache" in err
    assert "could not be warmed" in out
