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

    ANCHORED ON THE DEPLOY ITSELF. This used to find the console's
    `--set-env-vars "^@^CLOUD_SQL_CONNECTION_NAME` and back up to the
    deploy before it. The console moved to a `^|^` delimiter, the anchor
    landed on the smoke job's list instead, and the slice ran from the
    console deploy through the smoke job -- so a secret set only on a job
    passed as the console's.
    """
    text = CONSOLE.read_text()
    step = text.index('gcloud run deploy "${_SERVICE}"')
    return text[step : text.index("\n\n", step)]


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
        # Publishing. Without it a publish pins the data and never starts
        # the publish workflow: `notify_published` logs "not configured"
        # and returns. No deploy set it from the day the dispatch was
        # written (2026-08-22) until 2026-09-30.
        "GITHUB_DISPATCH_TOKEN",
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
        "GITHUB_DISPATCH_REPO",
    ],
)
def test_the_console_deploy_carries_every_variable(name):
    assert name in _console_deploy(), f"{name} is not set by the deploy"


def test_mail_is_configured_by_both_halves_or_neither():
    """The backend switches on both being present. One without the other
    is a console that thinks it can send and cannot."""
    step = _console_deploy()
    assert ("GMAIL_CREDENTIALS_JSON" in step) == ("GMAIL_DELEGATED_USER" in step)


def test_publishing_is_configured_by_both_halves_or_neither():
    """`notify_published` needs the repository and the token together; one
    without the other is a publish that says it dispatched and did not."""
    step = _console_deploy()
    assert ("GITHUB_DISPATCH_TOKEN" in step) == ("GITHUB_DISPATCH_REPO" in step)


def test_the_dispatch_names_this_repository():
    """The workflow that listens for `publish-visuals` lives here."""
    step = _console_deploy()
    assert "GITHUB_DISPATCH_REPO=LocalNewsImpact/datadesk" in step
    assert (ROOT / ".github/workflows/publish.yml").read_text().count("publish-visuals")


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


def test_every_scheduled_job_is_re_pinned_by_the_release():
    """A Cloud Run job pins its image when it is deployed and runs that one
    for ever; nothing about it follows the service.

    So a job deployed once goes on running that build however many times
    its schedule fires. `datadesk-scan-sources` was created on 25 August
    and was still pinned to that day's image while the console served a
    later one -- a check added to the flag vocabulary would never have run,
    and the schedule would have reported success every morning.

    Anything with a schedule therefore has to be re-pinned here, beside the
    deploy that builds the image.
    """
    pipeline = CONSOLE.read_text()
    scheduled = {
        # infra/keepwarm.sh, infra/scan_sources.sh: the jobs a Cloud
        # Scheduler entry points at.
        "${_SERVICE}-warm",
        "${_SERVICE}-scan-sources",
    }
    for job in scheduled:
        assert f'gcloud run jobs deploy "{job}"' in pipeline, (
            f"{job} runs on a schedule and is not re-pinned by the release, "
            "so it will go on running whatever image it was made with"
        )
        # ...to this build, not to a tag that floats or an older one.
        deploy = pipeline.split(f'gcloud run jobs deploy "{job}"', 1)[1]
        assert '--image "$$IMAGE"' in deploy.split("gcloud run jobs execute")[0]
