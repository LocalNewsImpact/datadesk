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
