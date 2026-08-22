"""The read-only crawler connection and the dataset row counts.

The crawler alias in tests is an empty sqlite database (no migrations may
land there — explorer.routers.CrawlerRouter). The fixture builds just
enough of the crawler's real schema (articles → candidate_links →
dataset_sources → datasets, the join path its own enrichment repository
uses) to prove the query, and its absence proves the degraded path.
"""

import pytest
from django.contrib.auth.models import Group, User
from django.db import connections

from explorer.crawler import dataset_row_counts

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def crawler_rows(crawler_schema):
    """Two datasets: one with two articles, one with none."""
    with connections["crawler"].cursor() as c:
        c.execute(
            "INSERT INTO datasets (id, slug, label) "
            "VALUES ('d1', 'missouri', 'Missouri')"
        )
        c.execute(
            "INSERT INTO datasets (id, slug, label) "
            "VALUES ('d2', 'lehigh', 'Lehigh Valley')"
        )
        c.execute(
            "INSERT INTO dataset_sources (id, dataset_id, source_id) "
            "VALUES ('ds1', 'd1', 's1')"
        )
        c.execute("INSERT INTO candidate_links (id, source_id) VALUES ('cl1', 's1')")
        c.execute("INSERT INTO articles (id, candidate_link_id) VALUES ('a1', 'cl1')")
        c.execute("INSERT INTO articles (id, candidate_link_id) VALUES ('a2', 'cl1')")


def test_counts_per_dataset(crawler_rows):
    assert dataset_row_counts() == [
        {"slug": "lehigh", "label": "Lehigh Valley", "articles": 0},
        {"slug": "missouri", "label": "Missouri", "articles": 2},
    ]


def test_connected_but_empty_is_a_list(crawler_schema):
    assert dataset_row_counts() == []


def test_missing_tables_reads_as_not_connected():
    """The local fallback alias is an empty sqlite file: None, not a crash."""
    assert dataset_row_counts() is None


def test_counts_are_cached(crawler_rows):
    first = dataset_row_counts()
    with connections["crawler"].cursor() as c:
        c.execute("INSERT INTO articles (id, candidate_link_id) VALUES ('a3', 'cl1')")
    assert dataset_row_counts() == first


def _viewer(email="viewer@localnewsimpact.org"):
    user = User.objects.create_user(username=email, email=email)
    user.groups.add(Group.objects.get(name="viewer"))
    return user


def test_landing_shows_row_counts(client, crawler_rows):
    """The Phase 0 exit test (SCOPE.md §4): a signed-in viewer sees live
    row counts per dataset."""
    client.force_login(_viewer())
    response = client.get("/")
    content = response.content.decode()
    assert "Missouri" in content
    assert "Lehigh Valley" in content


def test_landing_degrades_without_crawler_db(client):
    client.force_login(_viewer())
    response = client.get("/")
    assert response.status_code == 200
    assert "not connected" in response.content.decode()


def test_landing_shows_no_data_without_a_role(client, crawler_rows):
    email = "norole@localnewsimpact.org"
    client.force_login(User.objects.create_user(username=email, email=email))
    response = client.get("/")
    assert response.status_code == 200
    assert "Missouri" not in response.content.decode()


def test_no_migrations_reach_the_crawler_alias():
    from explorer.routers import CrawlerRouter

    router = CrawlerRouter()
    assert router.allow_migrate("crawler", "explorer") is False
    assert router.allow_migrate("default", "explorer") is None


def test_json_fields_accept_driver_decoded_values():
    """The crawler's JSON columns are Postgres `json`, not `jsonb`, so
    psycopg3 hands Django a parsed dict and plain JSONField would call
    json.loads on it (production 500s, 2026-08-22). sqlite tests cannot
    reproduce the driver behaviour, so exercise from_db_value directly.
    """
    from django.db import connections

    from explorer.models import Source

    field = Source._meta.get_field("meta")
    connection = connections["crawler"]
    assert field.from_db_value({"state": "MO"}, None, connection) == {"state": "MO"}
    assert field.from_db_value([1, 2], None, connection) == [1, 2]
    # Strings still parse, for backends that hand over raw JSON text.
    assert field.from_db_value('{"state": "MO"}', None, connection) == {"state": "MO"}
    assert field.from_db_value(None, None, connection) is None
