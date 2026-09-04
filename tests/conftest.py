"""Shared test fixtures."""

import os

import pytest


def pytest_report_header(config):
    """Say which database the run is against, at the top of the output.

    A run without one falls back to sqlite, which accepts SQL Postgres
    refuses -- that is how a `?|` on a text column and a DISTINCT over a
    `json` column both shipped green. `make test` and CI configure
    Postgres; a bare `pytest` does not, and should say so rather than
    pass quietly.
    """
    host = os.environ["DATADESK_TEST_DB_HOST"]
    port = os.environ.get("DATADESK_TEST_DB_PORT", "5432")
    return f"database: postgres at {host}:{port}"


NO_DATABASE = (
    "No test database. This suite runs on Postgres, because production "
    "does -- sqlite accepts SQL Postgres refuses, and three defects "
    "reached production through a green sqlite run.\n\n"
    "    make test        starts the database and runs this\n"
    "    make test-db     starts it on its own\n\n"
    "Set DATADESK_TEST_DB_HOST/PORT/USER/PASSWORD to point somewhere else."
)


def pytest_configure(config):
    # Refused rather than warned. Without a database the run falls back to
    # sqlite, where the crawler fixtures fail on DDL sqlite cannot parse --
    # a wall of "near AS: syntax error" that says nothing about the cause.
    if "DATADESK_TEST_DB_HOST" not in os.environ:
        raise pytest.UsageError(NO_DATABASE)


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
def fresh_cache(settings):
    """A per-process cache, emptied around every test.

    Production shares one cache across instances through a database
    table, because the service scales to zero and a per-process cache is
    empty on most requests. Tests want the opposite: isolation, no
    database round trip, and no dependence on the `django_db` mark just
    to clear a cache between tests.
    """
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


# Just enough of the crawler's real schema (MizzouNewsCrawler
# src/models/__init__.py) for the unmanaged explorer models and the raw
# row-count SQL.
#
# The column types are production's, read from information_schema on the
# live database rather than guessed, because the types are what the
# suite is for. `articles.metadata` is `json`, not text: a `json` column
# has no equality operator, so DISTINCT over a row containing one fails
# on Postgres and passes on sqlite. `evidence` is text, not json, so a
# JSON key operator applied to it fails the same way. Declaring these
# TEXT would let both defects through again.
#
# The crawler alias in tests is an empty Postgres database — CrawlerRouter
# keeps migrations out — so tables exist only where a test asks for them,
# and their absence proves the degraded paths.
_CRAWLER_TABLES = {
    "datasets": (
        "(id VARCHAR PRIMARY KEY, slug VARCHAR, label VARCHAR, name "
        "VARCHAR, description TEXT, metadata JSON, cron_enabled BOOLEAN, "
        "owner_name TEXT, owner_email TEXT)"
    ),
    "sources": (
        "(id VARCHAR PRIMARY KEY, host VARCHAR, host_norm VARCHAR, "
        "canonical_name VARCHAR, city VARCHAR, county VARCHAR, owner "
        "VARCHAR, type VARCHAR, status VARCHAR, metadata JSON, "
        "rss_consecutive_failures INTEGER, rss_transient_failures JSONB, "
        "requires_login BOOLEAN DEFAULT FALSE, auth_type VARCHAR(32), "
        "auth_secret_name VARCHAR(128), auth_config JSON, has_paywall "
        "BOOLEAN DEFAULT FALSE, subscription_cost NUMERIC(10, 2), "
        "subscription_period VARCHAR(16), login_url TEXT)"
    ),
    "gazetteer": (
        "(id VARCHAR PRIMARY KEY, dataset_id VARCHAR, source_id VARCHAR, "
        "category VARCHAR, created_at TIMESTAMP)"
    ),
    "dataset_sources": (
        "(id VARCHAR PRIMARY KEY, dataset_id VARCHAR, source_id VARCHAR)"
    ),
    "candidate_links": (
        "(id VARCHAR PRIMARY KEY, url VARCHAR, source VARCHAR, source_id "
        "VARCHAR, dataset_id VARCHAR)"
    ),
    "articles": (
        "(id VARCHAR PRIMARY KEY, candidate_link_id VARCHAR, url VARCHAR, "
        "title TEXT, author VARCHAR, publish_date TIMESTAMP, content TEXT, "
        "text TEXT, text_excerpt VARCHAR(500), raw_gcs_path VARCHAR, "
        "enrichment_attempts SMALLINT, metadata JSON, status VARCHAR, "
        "wire_check_status VARCHAR, wire JSON, created_at TIMESTAMP, "
        "primary_label VARCHAR, primary_label_confidence DOUBLE PRECISION, "
        "alternate_label VARCHAR, alternate_label_confidence DOUBLE "
        "PRECISION)"
    ),
    "article_enrichment": (
        "(article_id TEXT PRIMARY KEY, profile_version INTEGER, skip_reason "
        "TEXT, model TEXT, cost_usd NUMERIC(10, 6), enriched_at TIMESTAMPTZ, "
        "is_news_content BOOLEAN, content_gate_reason TEXT, scope TEXT, "
        "scope_confidence DOUBLE PRECISION, subject TEXT, "
        "subject_confidence DOUBLE PRECISION, topic TEXT, topic_confidence "
        "DOUBLE PRECISION, format TEXT, format_confidence DOUBLE PRECISION, "
        "timeframe TEXT, timeframe_confidence DOUBLE PRECISION, user_need "
        "TEXT, user_need_confidence DOUBLE PRECISION, rationales JSON, "
        "point_place TEXT, point_method TEXT, point_geoid TEXT, "
        "point_geoid_level TEXT, point_lat DOUBLE PRECISION, point_lon "
        "DOUBLE PRECISION, point_zcta VARCHAR(5), geoids TEXT, "
        "geo_skip_reason TEXT)"
    ),
    "article_geoids": (
        "(id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
        "article_id TEXT, geoid TEXT, geoid_level TEXT, is_primary BOOLEAN, "
        "source TEXT)"
    ),
    "article_people": (
        "(id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
        "article_id TEXT, name TEXT, sort_key TEXT, title TEXT, affiliation "
        "TEXT, person_type TEXT, role_in_story TEXT, nature TEXT, "
        "public_figure BOOLEAN, mention_count INTEGER, quotes JSON)"
    ),
    "article_organizations": (
        "(id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
        "article_id TEXT, name TEXT, org_type TEXT, boundary TEXT, "
        "role_in_story TEXT, nature TEXT, mention_count INTEGER)"
    ),
    "article_places": (
        "(id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
        "article_id TEXT, full_name TEXT, place_type TEXT, city TEXT, "
        "county TEXT, state TEXT, address TEXT, description TEXT, "
        "mention_text TEXT, is_point BOOLEAN, lat DOUBLE PRECISION, lon "
        "DOUBLE PRECISION, geocoder TEXT, geoid TEXT, geoid_level TEXT)"
    ),
    "content_type_detection_telemetry": (
        "(id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
        "article_id VARCHAR, detected_type VARCHAR, detection_method "
        "VARCHAR, confidence_score DOUBLE PRECISION, reason VARCHAR, "
        "evidence TEXT)"
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
