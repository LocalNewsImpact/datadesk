"""`reconcile_queues --apply` needs somebody to blame.

The audited write path records who made each change, and `--actor`
resolves an email to a `User`. On a schedule there is no person: the
Cloud Run job passes its own service account, and until this there was no
user with that address -- so every firing would have exited with "no user
with email ..." and written nothing, every night, silently as far as
anybody reading the console was concerned.
"""

import pytest
from django.contrib.auth.models import User

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

ACTOR_EMAIL = "datadesk-run@lnic-datadesk.iam.gserviceaccount.com"


def test_the_actor_the_scheduled_job_names_exists():
    """The job's args are `--actor <service account>`; the command looks
    that up as a User.email. These have to be the same string."""
    assert User.objects.filter(email=ACTOR_EMAIL).exists()


def test_it_cannot_log_in():
    """It is a name in an audit trail, not a login. Creating an account
    to satisfy a foreign key must not open a door."""
    actor = User.objects.get(email=ACTOR_EMAIL)
    assert not actor.is_active
    assert not actor.is_staff
    assert not actor.is_superuser
    assert not actor.has_usable_password()


def test_the_cloud_run_job_names_the_same_address():
    """The address lives in two places -- the migration and the release
    that deploys the job -- and a change to one is silent in the other."""
    from pathlib import Path

    build = (
        Path(__file__).resolve().parents[1] / "gcp/cloudbuild/cloudbuild-datadesk.yaml"
    ).read_text()
    step = build.split("reconcile-job")[1].split("- name:")[0]
    assert "--actor" in step
    # The release passes ${_RUN_SA}; the infra script passes it literally.
    assert "_RUN_SA" in step or ACTOR_EMAIL in step


def test_the_infra_script_names_the_same_address():
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[1] / "infra/reconcile_queues.sh"
    ).read_text()
    assert ACTOR_EMAIL in script or "datadesk-run@${PROJECT}" in script


def test_applying_without_an_actor_is_refused(crawler_schema):
    """An audited write with no writer records a change nobody made."""
    from io import StringIO

    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="actor"):
        call_command("reconcile_queues", "--apply", stdout=StringIO())


def test_it_does_not_appear_among_the_people(client):
    """The accounts page is about who can sign in. This account cannot,
    by design -- so listing it puts a machine in a list of people, and
    worse, in the DISABLED list, where it reads as somebody whose access
    was revoked.

    Found by two existing tests failing the moment the migration landed,
    which is the accounts admin saying the same thing.
    """
    from accounts.views import _people

    emails = {entry["user"].email for entry in _people()}
    assert ACTOR_EMAIL not in emails
    # And it is still there to be recorded against.
    assert User.objects.filter(email=ACTOR_EMAIL).exists()
