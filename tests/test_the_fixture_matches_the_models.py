"""The test fixture has to carry every column the models name.

tests/conftest.py builds a subset of the crawler's schema. A model that
gains a field the fixture does not have fails only on the paths some
test happens to touch, and passes everywhere else -- so a column can be
half-covered and look fine.

This is the offline half of the check. The other half is
`manage.py check_crawler_schema`, which compares the same models against
the crawler's real schema and needs a connection to it. Together:
fixture covers the models, models match the crawler.
"""

from django.apps import apps

from tests.conftest import _CRAWLER_TABLES


def _fixture_columns():
    """Column names per table, parsed from the fixture's DDL."""
    tables = {}
    for table, ddl in _CRAWLER_TABLES.items():
        names = set()
        depth = 0
        current = []
        # Split on commas that are not inside a type's parentheses, so
        # `NUMERIC(10, 2)` stays one column.
        for char in ddl[1:-1]:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            if char == "," and depth == 0:
                names.add("".join(current).strip().split()[0])
                current = []
            else:
                current.append(char)
        if current:
            names.add("".join(current).strip().split()[0])
        tables[table] = names
    return tables


def _crawler_models():
    return [
        model
        for model in apps.get_app_config("explorer").get_models()
        if getattr(model, "crawler_db", False)
    ]


def test_every_model_has_a_table_in_the_fixture():
    fixture = _fixture_columns()
    for model in _crawler_models():
        assert model._meta.db_table in fixture, (
            f"{model.__name__} reads `{model._meta.db_table}`, which "
            "tests/conftest.py does not create"
        )


def test_every_column_a_model_names_is_in_the_fixture():
    fixture = _fixture_columns()
    missing = [
        f"{model.__name__}.{field.name} -> {model._meta.db_table}.{field.column}"
        for model in _crawler_models()
        for field in model._meta.local_fields
        if field.column not in fixture.get(model._meta.db_table, ())
    ]
    assert (
        not missing
    ), "columns the models read that the fixture does not create: " + "; ".join(missing)


def test_the_fixture_declares_the_types_that_matter():
    """Not every type, but the ones whose mis-declaration has already
    reached production: `articles.metadata` is `json` and not text (a
    plain JSONField over it 500s), `evidence` is text and not json (a key
    operator over it has no operator), and the telemetry's id is an
    integer and not text."""
    articles = _CRAWLER_TABLES["articles"]
    assert "metadata JSON" in articles, "articles.metadata is `json` in the crawler"
    telemetry = _CRAWLER_TABLES["content_type_detection_telemetry"]
    assert "evidence TEXT" in telemetry, "evidence is text in the crawler, not json"
    assert "id INTEGER" in telemetry, "the telemetry's id is an integer"
