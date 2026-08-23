"""Warming the dashboard's caches.

Every expensive dashboard read is cached, but a cache only helps the
second reader — the first pays the full cost, measured between ten and
twenty-seven seconds depending on how much of the corpus Postgres
happens to be holding. Warming moves that cost off whoever arrives
first and onto a deploy or a schedule.
"""

import pytest
from django.core.cache import cache
from django.core.management import call_command

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

KEYS = (
    "explorer.dataset_row_counts",
    "explorer.recorded_costs",
    "explorer.corpus_summary",
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
    for label in ("dataset row counts", "recorded costs", "corpus summary"):
        assert label in out
    assert "caches warm" in out


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
