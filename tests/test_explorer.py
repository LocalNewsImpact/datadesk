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
from django.db import models as models_module

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


def test_every_explorer_model_reads_from_the_crawler_alias():
    """Adding a model to explorer must not silently query `default`."""
    from django.apps import apps

    from explorer.routers import CrawlerRouter

    router = CrawlerRouter()
    models = apps.get_app_config("explorer").get_models()
    assert models, "explorer declares no models"
    for model in models:
        assert router.db_for_read(model) == "crawler", model.__name__
        assert model._meta.managed is False, model.__name__
        assert model._meta.db_table, model.__name__


def test_application_models_stay_on_the_default_alias():
    from django.apps import apps

    from explorer.routers import CrawlerRouter

    router = CrawlerRouter()
    for app_label in ("audit", "review", "visuals"):
        for model in apps.get_app_config(app_label).get_models():
            assert router.db_for_read(model) is None, model.__name__
            assert router.db_for_write(model) is None, model.__name__


def test_entity_tables_match_the_crawler_schema():
    """Table names are the crawler's (docs/crawler_schema.txt), not
    Django's app_model default."""
    from explorer.models import (
        ArticleGeoid,
        ArticleOrganization,
        ArticlePerson,
        ArticlePlace,
    )

    assert ArticleGeoid._meta.db_table == "article_geoids"
    assert ArticlePerson._meta.db_table == "article_people"
    assert ArticleOrganization._meta.db_table == "article_organizations"
    assert ArticlePlace._meta.db_table == "article_places"


def test_id_columns_are_strings_not_uuids():
    """Every id in these tables is `character varying`, and some values
    are not UUIDs ('manual_art_...'). A UUIDField would reject them."""
    from explorer.models import Article, ArticleEnrichment, Dataset, Source

    for model, field in (
        (Article, "id"),
        (Dataset, "id"),
        (Source, "id"),
        (ArticleEnrichment, "article_id"),
    ):
        column = model._meta.get_field(field)
        assert not isinstance(column, models_module.UUIDField), model.__name__


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


# --- the Census gazetteer: codes and names travel together ------------------


def test_geoid_names_resolve_by_length():
    from datasets.geo import name_for_geoid

    assert name_for_geoid("29") == ("Missouri", "state")
    assert name_for_geoid("29019") == ("Boone County, MO", "county")
    assert name_for_geoid("2915670")[1] == "place"
    # The LSAD suffix the gazetteer appends is not part of the name.
    assert "city" not in name_for_geoid("2915670")[0]


def test_place_geoids_are_never_resolved_from_a_prefix():
    """Place codes do not nest inside their county — county 29601 is not
    an ancestor of place 2960176 — so a prefix must never name a place."""
    from datasets.geo import name_for_geoid

    name, kind = name_for_geoid("2960176")
    assert kind == "place"
    assert "County" not in (name or "")


def test_tract_and_block_fall_back_to_the_containing_county():
    from datasets.geo import name_for_geoid

    name, kind = name_for_geoid("29019000100")
    assert kind == "containing"
    assert name == "Boone County, MO"
    # The claim's own recorded place beats a county approximation.
    assert name_for_geoid("29019000100", fallback="Columbia") == ("Columbia", "given")


def test_unknown_code_resolves_to_nothing_rather_than_a_guess():
    from datasets.geo import name_for_geoid

    assert name_for_geoid("9999999") == (None, "unresolved")
    assert name_for_geoid("") == (None, "unresolved")
    assert name_for_geoid(None) == (None, "unresolved")


def test_level_for_geoid():
    from datasets.geo import level_for_geoid

    assert level_for_geoid("29") == "state"
    assert level_for_geoid("29019") == "county"
    assert level_for_geoid("2915670") == "place"
    assert level_for_geoid("29019000100") == "tract"
    assert level_for_geoid("290190001001000") == "block"
    assert level_for_geoid("123") is None
