"""Nothing tells this application when the crawler changes a column.

The models are unmanaged: no migration here creates the crawler's tables
and no test here reads them. A column renamed, dropped or retyped over
there leaves the suite green and breaks a page.

`check_crawler_schema` compares the models against a live schema. These
tests run it against the fixture schema -- which is Postgres now, and
carries production's column types -- so they prove the command works and
that the fixture and the models agree on every type.
"""

import pytest
from django.core.management import call_command
from django.db import models

from explorer.management.commands.check_crawler_schema import _expected, check
from explorer.models import ArticleEnrichment, ContentTypeDetection, DecodedJSONField


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_models_agree_with_the_schema_they_are_tested_against(crawler_schema):
    """Every column type in tests/conftest.py is one its model can read.
    A fixture that disagreed would prove the opposite of what it is for."""
    assert check("crawler") == []


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_json_column_under_a_plain_json_field_is_reported(
    crawler_schema, monkeypatch
):
    """The defect that 500'd the review queue: psycopg3 returns a `json`
    column already parsed and JSONField calls json.loads on the dict."""
    from explorer.models import Article

    field = Article._meta.get_field("metadata")
    assert isinstance(field, DecodedJSONField), "the fix under test"
    plain = models.JSONField(null=True)
    plain.set_attributes_from_name("metadata")
    plain.model = Article
    monkeypatch.setattr(
        Article._meta,
        "local_fields",
        [plain if f.name == "metadata" else f for f in Article._meta.local_fields],
    )
    problems = check("crawler")
    assert any("plain JSONField over a `json` column" in p for p in problems), problems


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_field_over_the_wrong_type_is_reported(crawler_schema, monkeypatch):
    """A TextField over an integer column -- what
    ContentTypeDetection.id was, and what nobody noticed because sqlite
    stores whatever it is given."""
    wrong = models.TextField()
    wrong.set_attributes_from_name("confidence_score")
    wrong.model = ContentTypeDetection
    monkeypatch.setattr(
        ContentTypeDetection._meta,
        "local_fields",
        [
            wrong if f.name == "confidence_score" else f
            for f in ContentTypeDetection._meta.local_fields
        ],
    )
    problems = check("crawler")
    assert any("confidence_score" in p and "double precision" in p for p in problems)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_missing_column_is_reported(crawler_schema, monkeypatch):
    """A column the crawler dropped or renamed."""
    gone = models.TextField()
    gone.set_attributes_from_name("a_column_the_crawler_does_not_have")
    gone.model = ContentTypeDetection
    monkeypatch.setattr(
        ContentTypeDetection._meta,
        "local_fields",
        list(ContentTypeDetection._meta.local_fields) + [gone],
    )
    problems = check("crawler")
    assert any("a_column_the_crawler_does_not_have" in p for p in problems)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_missing_table_is_reported():
    """No fixture, so no tables: every model is reported rather than the
    command passing on an empty database."""
    problems = check("crawler")
    assert len(problems) >= 12
    assert all("no table" in p for p in problems)


def test_a_foreign_key_is_checked_against_what_it_points_at():
    """The column holds the target's type, not an integer id."""
    field = ArticleEnrichment._meta.get_field("article")
    assert _expected(field) == _expected(ArticleEnrichment.article.field.target_field)


def test_an_unclassified_field_is_not_guessed_at():
    """Better silent than wrong: a field this does not know about is
    skipped rather than reported against a made-up expectation."""

    class Odd(models.Field):
        pass

    assert _expected(Odd()) is None


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_command_refuses_a_database_it_cannot_learn_anything_from(monkeypatch):
    """Run against sqlite it would report every table missing, which is
    noise, not drift."""
    from django.db import connections

    monkeypatch.setattr(type(connections["crawler"]), "vendor", "sqlite", raising=False)
    with pytest.raises(SystemExit):
        call_command("check_crawler_schema")
