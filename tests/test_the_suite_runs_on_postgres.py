"""The suite runs on the database production runs.

It used to run on sqlite, and sqlite accepts SQL Postgres refuses. Three
defects reached production green:

1. `evidence` is TEXT in the crawler, not jsonb. Declared a JSONField,
   the ORM emitted `?|`, which Postgres has no operator for on text.
2. `.distinct()` over selected rows failed with "could not identify an
   equality operator for type json" -- `articles` carries json columns.
3. `Article.metadata` is Postgres `json`, which psycopg3 hands back
   already parsed. A plain JSONField called json.loads on the dict and
   raised, which was a 500 on the review queue for any held article.

The first two surfaced to users as "crawler database not connected",
because the view catches DatabaseError and cannot tell a broken query
from a broken connection. All three now fail in the suite instead, so
there is nothing here asserting the shape of the code that avoids them --
the queries run.

What is left to assert is that the suite has not quietly fallen back.
"""

from django.db import connections


def test_both_aliases_are_postgres():
    """A settings change that dropped either alias back to sqlite would
    make the suite green and prove nothing, which is the condition this
    whole change exists to end."""
    for alias in ("default", "crawler"):
        assert connections[alias].vendor == "postgresql", (
            f"the {alias} alias is {connections[alias].vendor}; "
            "the suite is meant to run on Postgres (make test)"
        )
