"""Every model the console can CREATE needs an INSERT grant, and its
sequence needs one too.

`services.CREATABLE` says what the audited write path may create.
Postgres decides whether it can. Nothing connected the two, so a table
added to `CREATABLE` was tested everywhere -- unit tests, integration
tests on a Postgres where the test user owns everything -- and failed
only in production, as the role the job actually runs as.

That is what happened. `pipeline_rework` reached `CREATABLE` with SELECT
and no INSERT; the nightly reconciliation moved two articles to `cleaned`
and then died on `permission denied for table pipeline_rework`. And
`article_places_manual` worked only because somebody had granted it by
hand: the grant was in production and in no file, so rebuilding the role
from `create_crawler_write_role.sql` would have broken manual geography.

These read the boundary file. It is the one artifact that says what the
role may do.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GRANTS = ROOT / "infra/sql/create_crawler_write_role.sql"
SQL = GRANTS.read_text()


def _creatable_crawler_tables():
    """The crawler-owned tables the console creates rows in.

    Datasets and sources live in the crawler database too, but they are
    granted column by column above; these are the ones granted whole.
    """
    import django

    django.setup()
    from explorer.models import CrawlerModel
    from review.services import CREATABLE

    return sorted(
        model._meta.db_table
        for model in CREATABLE
        if issubclass(model, CrawlerModel)
        and not _granted_by_column(model._meta.db_table)
    )


def _granted_by_column(table):
    return bool(re.search(rf"GRANT INSERT \([^)]+\)\s*\n?\s*ON {table}\b", SQL))


@pytest.mark.parametrize("table", ["article_places_manual", "pipeline_rework"])
def test_a_whole_table_insert_is_granted(table):
    assert re.search(rf"GRANT INSERT ON {table} TO datadesk_rw", SQL), (
        f"{table} is creatable and has no INSERT grant. It will fail as "
        "datadesk_rw and nowhere else."
    )


@pytest.mark.parametrize("table", ["article_places_manual", "pipeline_rework"])
def test_the_sequence_is_granted_too(table):
    """An INSERT that cannot reach the sequence fails with "permission
    denied for sequence", which reads like a different problem."""
    assert re.search(
        rf"GRANT USAGE ON SEQUENCE {table}_id_seq TO datadesk_rw", SQL
    ), f"{table} takes its id from a sequence and the role cannot use it"


def test_every_creatable_crawler_table_is_covered():
    """The general form, so the next table added to `CREATABLE` fails here
    rather than in production at 02:30."""
    missing = [
        table
        for table in _creatable_crawler_tables()
        # `GRANT INSERT, UPDATE ON …` counts: the grant is what matters, not
        # whether it was written on a line of its own.
        if not re.search(rf"GRANT [^;]*INSERT[^;]*ON {table} TO datadesk_rw", SQL)
    ]
    assert missing == [], f"creatable with no INSERT grant: {missing}"


#: The one creatable table the console may also revise, and why.
#: `byline_normalizations` has a unique constraint on (dataset, byline string),
#: so there is one row per string and re-deciding one is the same row. Every
#: other creatable table takes a second row and keeps the first as history.
REVISABLE_BY_DESIGN = {"byline_normalizations"}


def test_nothing_creatable_is_granted_update_or_delete():
    """A row the console creates is a record of what a person or a
    decision said. The crawler closes a rework row; nobody revises one."""
    for table in _creatable_crawler_tables():
        if table in REVISABLE_BY_DESIGN:
            continue
        assert not re.search(rf"GRANT (UPDATE|DELETE)[^;]*ON {table}\b", SQL), table


def test_every_deletable_crawler_table_is_granted():
    """The console removes a byline candidate it has worked, and the queue is a
    cache the crawler rewrites nightly. Without the grant the decision is stored
    and the row stays, so the reviewer answers it again tomorrow."""
    import django

    django.setup()
    from explorer.models import CrawlerModel
    from review.services import DELETABLE

    missing = [
        model._meta.db_table
        for model in DELETABLE
        if issubclass(model, CrawlerModel)
        and not re.search(
            rf"GRANT [^;]*DELETE[^;]*ON {model._meta.db_table} TO datadesk_rw", SQL
        )
    ]
    assert missing == [], f"deletable with no DELETE grant: {missing}"


def test_every_writable_crawler_table_is_granted():
    """A column the console may write needs the privilege to write it, whole
    table or column by column.

    This is the check that was missing when the byline page shipped. The page's
    submissions were unit tested and green -- the suite creates its own database
    and owns every table in it -- and every submission failed in production with
    "permission denied for table byline_normalizations"."""
    import django

    django.setup()
    from explorer.models import CrawlerModel
    from review.services import WRITABLE

    missing = []
    for model in WRITABLE:
        if not issubclass(model, CrawlerModel):
            continue
        table = model._meta.db_table
        whole = re.search(rf"GRANT [^;]*UPDATE[^;]*ON {table} TO datadesk_rw", SQL)
        by_column = re.search(rf"GRANT UPDATE \([^)]+\)\s*\n?\s*ON {table}\b", SQL)
        if not whole and not by_column:
            missing.append(table)
    assert missing == [], f"writable with no UPDATE grant: {missing}"


def test_the_file_reports_what_it_granted():
    """It prints its own result, because the grant is invisible from the
    application side until something tries to write."""
    assert "whole-table grants held by datadesk_rw" in SQL
