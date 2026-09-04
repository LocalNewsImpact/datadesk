"""Does the crawler's schema still match what these models say it is?

The models in explorer/models.py are unmanaged: nothing in this
repository creates or migrates the crawler's tables, and nothing tells
this application when the crawler changes one. A column renamed,
dropped, or retyped over there leaves every test here green and breaks a
page over here.

That is not hypothetical. Every defect this command looks for has
already happened:

    evidence     JSONField over a `text` column   -> `?|` has no operator
    id           TextField over `integer`          -> insert fails
    metadata     JSONField over `json`             -> psycopg3 returns it
                                                     parsed and the field
                                                     calls json.loads on a
                                                     dict; 500

The suite cannot see any of this: it builds its own tables from
tests/conftest.py, so it proves the models agree with that fixture, not
with the crawler. This command reads the real schema and compares.

    python manage.py check_crawler_schema

Needs the crawler alias pointed at a real database -- the Cloud SQL Auth
Proxy locally, the unix socket on Cloud Run. Exits non-zero on drift, so
it can be scheduled.
"""

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connections, models

from explorer.models import DecodedJSONField

#: Which Postgres column types each field class can read correctly.
#: Anything outside this is the kind of mismatch that reaches production.
COMPATIBLE = {
    models.TextField: {"text", "character varying", "character"},
    models.CharField: {"text", "character varying", "character"},
    models.URLField: {"text", "character varying"},
    models.SlugField: {"text", "character varying"},
    models.EmailField: {"text", "character varying"},
    models.AutoField: {"integer"},
    models.BigAutoField: {"bigint"},
    models.IntegerField: {"integer", "smallint"},
    models.SmallIntegerField: {"smallint", "integer"},
    models.BigIntegerField: {"bigint", "integer"},
    models.PositiveIntegerField: {"integer", "smallint"},
    models.FloatField: {"double precision", "real"},
    models.DecimalField: {"numeric"},
    models.BooleanField: {"boolean"},
    models.DateTimeField: {"timestamp with time zone", "timestamp without time zone"},
    models.DateField: {"date"},
    models.UUIDField: {"uuid", "character varying", "text"},
}


def _expected(field):
    """The column types this field can read, or None if unclassified."""
    if isinstance(field, models.ForeignKey):
        return _expected(field.target_field)
    if isinstance(field, models.JSONField):
        # Split below: `json` and `jsonb` need different fields.
        return {"json", "jsonb"}
    for cls in type(field).__mro__:
        if cls in COMPATIBLE:
            return COMPATIBLE[cls]
    return None


def _live_columns(cursor, table):
    cursor.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s",
        [table],
    )
    return dict(cursor.fetchall())


def check(alias="crawler"):
    """Every disagreement between these models and the live schema."""
    problems = []
    crawler_models = [
        m
        for m in apps.get_app_config("explorer").get_models()
        if getattr(m, "crawler_db", False)
    ]
    with connections[alias].cursor() as cursor:
        for model in crawler_models:
            table = model._meta.db_table
            live = _live_columns(cursor, table)
            if not live:
                problems.append(f"{model.__name__}: no table `{table}`")
                continue
            for field in model._meta.local_fields:
                column = field.column
                actual = live.get(column)
                if actual is None:
                    problems.append(
                        f"{model.__name__}.{field.name}: no column "
                        f"`{table}.{column}`"
                    )
                    continue
                expected = _expected(field)
                if expected is None:
                    continue
                if actual not in expected:
                    problems.append(
                        f"{model.__name__}.{field.name}: "
                        f"{type(field).__name__} over `{actual}` "
                        f"({table}.{column}); expected one of "
                        f"{', '.join(sorted(expected))}"
                    )
                    continue
                # A `json` column arrives from psycopg3 already parsed.
                # Plain JSONField calls json.loads on the dict and raises.
                if (
                    actual == "json"
                    and isinstance(field, models.JSONField)
                    and not isinstance(field, DecodedJSONField)
                ):
                    problems.append(
                        f"{model.__name__}.{field.name}: plain JSONField over "
                        f"a `json` column ({table}.{column}). psycopg3 returns "
                        "it already parsed and JSONField calls json.loads on "
                        "the dict. Use DecodedJSONField."
                    )
    return problems


class Command(BaseCommand):
    help = "Compare the unmanaged crawler models against the live schema."

    def handle(self, *args, **options):
        alias = "crawler"
        if connections[alias].vendor != "postgresql":
            raise SystemExit(
                "The crawler alias is "
                f"{connections[alias].vendor}, not the real database. Point "
                "CRAWLER_DB_USER at it (Cloud SQL Auth Proxy locally)."
            )
        problems = check(alias)
        if not problems:
            self.stdout.write(
                self.style.SUCCESS("The models match the crawler's schema.")
            )
            return
        self.stderr.write(
            self.style.ERROR(f"{len(problems)} disagreements with the live schema:")
        )
        for problem in problems:
            self.stderr.write(f"  {problem}")
        raise SystemExit(1)
