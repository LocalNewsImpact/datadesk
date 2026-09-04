"""Telling a database that is not there from a query that is wrong.

Twelve places in this application catch `DatabaseError` and degrade --
render the page without the crawler's figures, return None, show a
notice. That is right for the case they were written for: a checkout
with no crawler connection configured, where the tables genuinely are
not there and the honest answer is "not connected".

It is wrong for every other `DatabaseError`. A query with an operator
the column does not support, or a DISTINCT over a type that has no
equality operator, is a defect in this repository, and reporting it as
an unreachable database sends whoever reads the page to check
credentials that are correct. That happened: `/review/queue/` told
somebody to set CRAWLER_DB_USER while CRAWLER_DB_USER was set.

So the catch has to ask which it is. `is_absent` is true only for the
failures that really mean "no database here":

- the connection could not be made at all (class 08, and psycopg's
  OperationalError with no SQLSTATE, which is what a refused TCP
  connection or a failed DNS lookup arrives as)
- authentication was refused (class 28)
- the table does not exist (42P01), which is how an empty or
  unprovisioned crawler alias presents, and sqlite's "no such table"

Everything else -- an undefined column or function, a type mismatch, a
missing operator, bad data -- is a bug, and `absent_or_raise` re-raises
it so it surfaces as a 500 with a traceback instead of a notice that
blames the network. A page that half-renders while lying about why is
worse than one that fails.
"""

import logging

from django.db import DatabaseError

log = logging.getLogger(__name__)

#: `undefined_table`. An alias pointed at a database that has not been
#: provisioned -- the local default, and every test that proves a
#: degraded path.
UNDEFINED_TABLE = "42P01"

#: `in_failed_sql_transaction`. Not a failure of its own: the query
#: before it broke the transaction and every statement until the rollback
#: reports this instead of anything useful. That earlier failure was
#: already classified here -- a defect would have been raised at its own
#: call site and this code would never run -- so reaching this means the
#: transaction was broken by an absent table, and the caller should
#: degrade rather than blame a query that is fine.
IN_FAILED_TRANSACTION = "25P02"

#: Connection exception, and invalid authorization. Postgres reports
#: these with a SQLSTATE; a connection that never opened has none.
CONNECTION_CLASSES = ("08", "28")


def sqlstate(exc):
    """The SQLSTATE psycopg recorded, following the exception chain.

    Django wraps the driver's exception, so the code is on `__cause__`.
    Returns None for sqlite and for a connection that never opened.
    """
    seen = 0
    while exc is not None and seen < 5:
        code = getattr(exc, "sqlstate", None)
        if code:
            return code
        exc = exc.__cause__
        seen += 1
    return None


def is_absent(exc):
    """True when the failure means there is no database to read.

    False for a query this repository got wrong, which is the
    distinction the callers exist to make.
    """
    if not isinstance(exc, DatabaseError):
        return False
    code = sqlstate(exc)
    if code is None:
        # sqlite says so in words; psycopg leaves a failed connection
        # with no SQLSTATE at all.
        text = str(exc).lower()
        if "no such table" in text or "no such column" in text:
            return True
        from django.db import OperationalError

        return isinstance(exc, OperationalError)
    if code in (UNDEFINED_TABLE, IN_FAILED_TRANSACTION):
        return True
    return code[:2] in CONNECTION_CLASSES


def absent_or_raise(exc, where):
    """Return None where the database is absent; re-raise where it is not.

    `where` names the caller, so a log line says which query broke
    without a traceback having to be read.

        try:
            ...
        except DatabaseError as exc:
            absent_or_raise(exc, "explorer.dataset_row_counts")
            return None
    """
    if is_absent(exc):
        log.debug("%s: no crawler database (%s)", where, sqlstate(exc) or "no sqlstate")
        return None
    log.error(
        "%s: the query failed with SQLSTATE %s -- this is a defect in the "
        "query, not a missing connection: %s",
        where,
        sqlstate(exc) or "none",
        exc,
    )
    raise exc
