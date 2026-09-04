"""A deploy has to prove more than that Django started.

`/_health` renders without touching the crawler, so a query that cannot
run against the real schema passes it and fails when somebody opens the
page. Both defects that reached production would have passed it.

`smoke_queries` runs the console's read paths against whatever the
crawler alias points at. The deploy runs it as a job on the candidate
image before traffic shifts (gcp/cloudbuild/cloudbuild-datadesk.yaml), so
a broken query holds the rollout.
"""

import pytest
from django.core.management import call_command

from explorer.management.commands import smoke_queries


@pytest.mark.django_db(databases=["default", "crawler"])
def test_every_read_path_runs(crawler_schema, capsys, django_user_model):
    """Against the fixture schema, which is Postgres and carries
    production's column types.

    With a real user, because the scoped paths narrow through group
    membership and running them as nobody exercises a different query.
    """
    django_user_model.objects.create_superuser("smoke@localnewsimpact.org", "x")
    call_command("smoke_queries")
    out = capsys.readouterr().out
    assert "read paths ran against postgresql" in out
    assert "FAIL" not in out


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_broken_read_path_fails_the_command(crawler_schema, monkeypatch, capsys):
    """The rollout has to stop. A check that raises is a non-zero exit,
    not a line in a log nobody reads."""

    def boom():
        raise RuntimeError("could not identify an equality operator for type json")

    real = smoke_queries._checks

    def one_broken():
        return list(real()) + [("a broken read path", boom)]

    monkeypatch.setattr(smoke_queries, "_checks", one_broken)
    with pytest.raises(SystemExit):
        call_command("smoke_queries")
    err = capsys.readouterr().err
    assert "FAIL  a broken read path" in err
    assert "equality operator" in err


@pytest.mark.django_db(databases=["default", "crawler"])
def test_missing_tables_fail_the_command_rather_than_passing_quietly():
    """No fixture: the tables are not there. A smoke test that treats an
    absent database as success proves nothing on the day it matters."""
    with pytest.raises(SystemExit):
        call_command("smoke_queries")


def test_it_refuses_a_database_that_proves_nothing(monkeypatch):
    """Run against sqlite it would pass while production was broken,
    which is the failure this whole exercise is about."""
    from django.db import connections

    monkeypatch.setattr(type(connections["crawler"]), "vendor", "sqlite", raising=False)
    with pytest.raises(SystemExit):
        call_command("smoke_queries")


def test_the_deploy_runs_it_before_shifting_traffic():
    """A smoke test after the shift is a post-mortem."""
    import yaml
    from django.conf import settings

    config = yaml.safe_load(
        (settings.BASE_DIR / "gcp/cloudbuild/cloudbuild-datadesk.yaml").read_text()
    )
    steps = {step["id"]: step for step in config["steps"] if "id" in step}
    assert "smoke-queries" in steps, "the deploy does not run the smoke check"
    assert "smoke_queries" in steps["smoke-queries"]["args"][-1]
    assert steps["shift"]["waitFor"] == [
        "smoke-queries"
    ], "traffic shifts without waiting for the smoke check"
