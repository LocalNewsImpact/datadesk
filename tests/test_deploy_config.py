"""What the deploy pipeline must set, asserted against the pipeline.

`gcloud run deploy --set-env-vars` and `--set-secrets` *replace* the
whole set. A variable added to the service by hand therefore survives
until the next deploy and then vanishes, which is what happened to the
mail credentials: wired, proved by a test send, and gone twenty minutes
later when a merge deployed.

So anything the application needs at runtime belongs in this file's
subject rather than in somebody's shell history.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "gcp/cloudbuild/cloudbuild-datadesk.yaml"


def _console_deploy():
    """The `gcloud run deploy` for the console, as one string.

    The data and sources front ends deploy from the same file with their
    own environments; this picks the console's, which is the one that
    signs people in and sends their mail.
    """
    text = CONSOLE.read_text()
    start = text.index('--set-env-vars "^@^CLOUD_SQL_CONNECTION_NAME')
    # Back up to the start of that deploy step, forward to the end of it.
    step = text.rindex("gcloud run deploy", 0, start)
    return text[step : text.index("\n\n", start)]


@pytest.mark.parametrize(
    "name",
    [
        "DJANGO_SECRET_KEY",
        "DB_PASSWORD",
        "CRAWLER_DB_PASSWORD",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        # Mail. Without it the console falls back to the console backend,
        # which means a set-password link is printed to a log nobody
        # reads and the person it was for never hears anything.
        "GMAIL_CREDENTIALS_JSON",
    ],
)
def test_the_console_deploy_carries_every_secret(name):
    assert name in _console_deploy(), f"{name} is not set by the deploy"


@pytest.mark.parametrize(
    "name",
    [
        "CLOUD_SQL_CONNECTION_NAME",
        "DB_NAME",
        "CRAWLER_DB_NAME",
        "ALLOWED_AUTH_DOMAINS",
        "DJANGO_ALLOWED_HOSTS",
        "SESSION_COOKIE_DOMAIN",
        "GMAIL_DELEGATED_USER",
    ],
)
def test_the_console_deploy_carries_every_variable(name):
    assert name in _console_deploy(), f"{name} is not set by the deploy"


def test_mail_is_configured_by_both_halves_or_neither():
    """The backend switches on both being present. One without the other
    is a console that thinks it can send and cannot."""
    step = _console_deploy()
    assert ("GMAIL_CREDENTIALS_JSON" in step) == ("GMAIL_DELEGATED_USER" in step)


def test_the_settings_read_what_the_deploy_sets():
    """The other half of the pair: a variable the pipeline sets and the
    settings never read is one somebody added for a reason that has
    since gone."""
    settings_text = (ROOT / "datadesk/settings.py").read_text()
    for name in ("GMAIL_CREDENTIALS_JSON", "GMAIL_DELEGATED_USER"):
        assert name in settings_text, f"nothing reads {name}"


# --- the delimiter ------------------------------------------------------------
#
# `--set-env-vars` takes a `^X^` prefix naming its separator, because the
# values contain commas of their own. Twice now that has gone wrong in
# opposite directions: a comma-joined list under an `@` delimiter, which
# made fifteen variables the value of the first; and a value containing
# the `@` that was the delimiter, which split an address in half and
# failed the deploy outright.
#
#     ERROR: argument --set-env-vars: Bad syntax for dict arg:
#            [localnewsimpact.org]


def _env_lists():
    """Every `--set-env-vars` in the deploy file, with its delimiter."""
    import re

    found = []
    for line in CONSOLE.read_text().splitlines():
        match = re.search(r'--set-env-vars "\^(.)\^(.*)"', line)
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def test_every_deploy_uses_a_delimiter_its_values_do_not_contain():
    lists = _env_lists()
    assert lists, "no environment lists found — has the flag changed shape?"
    for delimiter, body in lists:
        for entry in body.split(delimiter):
            assert "=" in entry, (
                f"{entry!r} is not NAME=value: the delimiter {delimiter!r} "
                f"appears inside a value and split it"
            )
            _name, _, value = entry.partition("=")
            assert (
                delimiter not in value
            ), f"{_name} contains the delimiter {delimiter!r}: {value!r}"


def test_every_variable_the_deploy_sets_has_a_name_and_a_value():
    """A bare item is what gcloud rejects, and the message names the
    fragment rather than the variable it came from -- so the failure
    reads as being about a domain rather than about a separator."""
    for delimiter, body in _env_lists():
        for entry in body.split(delimiter):
            name, _, value = entry.partition("=")
            assert name.strip(), f"an entry with no name: {entry!r}"
            assert value.strip(), f"{name} is set to nothing"
