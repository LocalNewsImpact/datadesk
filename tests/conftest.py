"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def plain_static_storage(settings):
    """Serve static files without the hashed manifest during tests.

    Production uses WhiteNoise's manifest storage, which refuses to render
    a page referencing a file collectstatic has not processed. That is the
    right behaviour when deployed and useless in a test run, where
    rendering a page would otherwise fail on missing CSS rather than on
    anything real. (Pattern from NewsSourceDirectory tests/conftest.py.)
    """
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }


@pytest.fixture(autouse=True)
def fresh_cache():
    """Explorer results and filter vocabularies are cached in-process;
    tests must not read each other's."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


# Just enough of the crawler's real schema (MizzouNewsCrawler
# src/models/__init__.py) for the unmanaged explorer models and the raw
# row-count SQL. The crawler alias in tests is an empty sqlite database —
# CrawlerRouter keeps migrations out — so tables exist only where a test
# asks for them, and their absence proves the degraded paths.
_CRAWLER_TABLES = {
    "datasets": "(id TEXT PRIMARY KEY, slug TEXT, label TEXT, name TEXT)",
    "sources": (
        "(id TEXT PRIMARY KEY, host TEXT, host_norm TEXT, canonical_name TEXT,"
        " city TEXT, county TEXT, status TEXT)"
    ),
    "dataset_sources": "(id TEXT PRIMARY KEY, dataset_id TEXT, source_id TEXT)",
    "candidate_links": (
        "(id TEXT PRIMARY KEY, url TEXT, source TEXT, source_id TEXT,"
        " dataset_id TEXT)"
    ),
    "articles": (
        "(id TEXT PRIMARY KEY, candidate_link_id TEXT, url TEXT, title TEXT,"
        " author TEXT, publish_date TIMESTAMP, text_excerpt TEXT, status TEXT,"
        " wire_check_status TEXT, created_at TIMESTAMP, primary_label TEXT,"
        " primary_label_confidence REAL, alternate_label TEXT,"
        " alternate_label_confidence REAL)"
    ),
    "article_enrichment": (
        "(article_id TEXT PRIMARY KEY, profile_version TEXT, skip_reason TEXT,"
        " model TEXT, cost_usd REAL, enriched_at TIMESTAMP,"
        " is_news_content INTEGER, content_gate_reason TEXT,"
        " scope TEXT, scope_confidence REAL, subject TEXT,"
        " subject_confidence REAL, topic TEXT, topic_confidence REAL,"
        " format TEXT, format_confidence REAL, timeframe TEXT,"
        " timeframe_confidence REAL, user_need TEXT, user_need_confidence REAL,"
        " rationales TEXT, point_place TEXT, point_method TEXT,"
        " point_geoid TEXT, point_geoid_level TEXT, point_lat REAL,"
        " point_lon REAL, point_zcta TEXT, geoids TEXT, geo_skip_reason TEXT)"
    ),
}


@pytest.fixture
def crawler_schema():
    from django.db import connections

    with connections["crawler"].cursor() as c:
        for table, columns in _CRAWLER_TABLES.items():
            c.execute(f"CREATE TABLE {table} {columns}")
    yield
    with connections["crawler"].cursor() as c:
        for table in reversed(list(_CRAWLER_TABLES)):
            c.execute(f"DROP TABLE {table}")
