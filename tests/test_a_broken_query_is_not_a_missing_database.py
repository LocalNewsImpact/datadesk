"""A query this repository got wrong must not be reported as an
unreachable database.

`/review/queue/` told somebody to set CRAWLER_DB_USER while
CRAWLER_DB_USER was set. Two queries could not run on Postgres, the view
caught DatabaseError, and every DatabaseError read as "not connected" --
so a defect in this repository presented as a problem with their
credentials, and the page half-rendered while saying something untrue.
"""

import logging

import pytest
from django.db import DatabaseError, OperationalError, ProgrammingError

from explorer.dberrors import absent_or_raise, is_absent, sqlstate


class _Driver(Exception):
    """Stands in for psycopg's exception, which carries the SQLSTATE."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.sqlstate = code


def _wrapped(django_class, code, message=""):
    """A Django DatabaseError over a driver error, as psycopg raises it."""
    exc = django_class(message or code)
    exc.__cause__ = _Driver(code, message)
    return exc


# --- what is genuinely absent ------------------------------------------------


def test_a_missing_table_is_an_absent_database():
    """An alias pointed at a database with no crawler tables. Every
    degraded path in the application exists for this."""
    assert is_absent(_wrapped(ProgrammingError, "42P01", "relation does not exist"))


def test_a_refused_connection_is_an_absent_database():
    """No SQLSTATE at all: the connection never opened."""
    assert is_absent(OperationalError("connection refused"))


def test_a_rejected_password_is_an_absent_database():
    assert is_absent(_wrapped(OperationalError, "28P01", "password authentication"))


def test_sqlite_says_it_in_words():
    """The local fallback alias is an empty sqlite file, and sqlite has
    no SQLSTATE to report."""
    assert is_absent(OperationalError("no such table: articles"))


def test_an_aborted_transaction_follows_something_already_classified():
    """25P02 is never the real failure -- the statement before it broke
    the transaction. A defect would have been raised at its own call
    site, so reaching this means an absent table did it."""
    assert is_absent(_wrapped(ProgrammingError, "25P02", "transaction is aborted"))


# --- what is a defect --------------------------------------------------------


def test_the_operator_defect_is_not_an_absent_database():
    """`evidence` is text and the ORM emitted `?|`. This is the failure
    that was reported to a user as a connection problem."""
    exc = _wrapped(ProgrammingError, "42883", "operator does not exist: text ?| text[]")
    assert not is_absent(exc)


def test_the_distinct_defect_is_not_an_absent_database():
    """DISTINCT over a row carrying a `json` column."""
    exc = _wrapped(
        ProgrammingError,
        "42883",
        "could not identify an equality operator for type json",
    )
    assert not is_absent(exc)


def test_a_missing_column_is_not_an_absent_database():
    """A model naming a column the crawler renamed. The tables are there;
    the query is wrong."""
    assert not is_absent(_wrapped(ProgrammingError, "42703", "column does not exist"))


# --- what the callers do with it ---------------------------------------------


def test_an_absent_database_lets_the_caller_degrade():
    assert absent_or_raise(_wrapped(ProgrammingError, "42P01"), "somewhere") is None


def test_a_defect_is_raised_rather_than_swallowed():
    """A 500 with a traceback, not a notice blaming the network. The page
    is worth less than knowing why it is wrong."""
    exc = _wrapped(ProgrammingError, "42883", "operator does not exist")
    with pytest.raises(DatabaseError):
        absent_or_raise(exc, "somewhere")


def test_a_defect_names_the_query_in_the_log(caplog):
    """So the log line identifies the caller without a traceback having
    to be read."""
    exc = _wrapped(ProgrammingError, "42883", "operator does not exist")
    with (
        caplog.at_level(logging.ERROR, logger="explorer.dberrors"),
        pytest.raises(DatabaseError),
    ):
        absent_or_raise(exc, "review.queue.vocab")
    assert "review.queue.vocab" in caplog.text
    assert "42883" in caplog.text
    assert "defect" in caplog.text


def test_the_sqlstate_is_read_through_djangos_wrapper():
    """Django wraps the driver's exception; the code is on __cause__."""
    assert sqlstate(_wrapped(ProgrammingError, "42P01")) == "42P01"
    assert sqlstate(OperationalError("no code here")) is None


def test_something_that_is_not_a_database_error_is_not_absent():
    assert not is_absent(ValueError("unrelated"))
