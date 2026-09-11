"""CI creates the database the settings point at.

The shared workflow's Postgres service names its database from
`postgres-db`, which defaults to `test`. This suite's test settings name
theirs `datadesk`. Nothing passed the input, so the database the settings
pointed at did not exist on any run this repository has ever made.

The suite never noticed, which is why it lasted. pytest-django creates
`test_datadesk` itself, and creating a database only needs a connection
to the `postgres` maintenance database. Every test ran.

What did notice is `manage.py makemigrations --check`, which `make test`
runs first. It connects to the named database to read `django_migrations`
and verify migration history, failed, warned, and carried on -- so every
green build in this repository printed

    RuntimeWarning: Got an error checking a consistent migration history
    ... FATAL: database "datadesk" does not exist

A warning on every build is one nobody can use, and this one reads
exactly like a broken database connection. It was reported as a failure
more than once.

Asserted as a pair rather than as a literal: the point is that the two
names AGREE, so renaming either one alone fails here instead of
reintroducing the warning.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/ci.yml").read_text()
SETTINGS = (ROOT / "datadesk/settings.py").read_text()


def _settings_default_name():
    """The NAME the test settings give the default connection."""
    match = re.search(
        r'DATABASES\["default"\]\s*=\s*\{\*\*_test_db,\s*"NAME":\s*"([^"]+)"\}',
        SETTINGS,
    )
    assert match, "the test settings no longer name the default database"
    return match.group(1)


def _workflow_postgres_db():
    match = re.search(r"^\s*postgres-db:\s*(\S+)\s*$", WORKFLOW, re.M)
    return match.group(1) if match else None


def test_ci_is_told_which_database_to_create():
    """Without this input the service falls back to `test` and the
    settings' database is never created."""
    assert _workflow_postgres_db() is not None, (
        "ci.yml passes no postgres-db, so the shared workflow creates "
        "`test` and the database settings.py names does not exist"
    )


def test_the_two_names_agree():
    """A rename on either side reintroduces the warning silently."""
    assert _workflow_postgres_db() == _settings_default_name()


def test_the_crawler_alias_does_not_need_one():
    """Django prefixes each test database with `test_`, so the crawler
    alias is created by the test run rather than by the service. Only
    the default connection is read before that happens."""
    assert 'DATABASES["crawler"] = {**_test_db, "NAME": "datadesk_crawler"}' in SETTINGS
    assert _workflow_postgres_db() != "datadesk_crawler"
