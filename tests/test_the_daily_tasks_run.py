"""The console's daily work, in one place.

Two of these had no schedule at all. `refresh_worklist` fills the To Do
counts every reviewer's landing page reads and had never run on one since
it was built, so the counts were whatever the last manual run left.
`find_repeated_bodies` would have said nothing until somebody remembered
it existed.

One job for the set rather than one each: a second Cloud Run job and a
second scheduler entry per task is how a task comes to have neither.
"""

from pathlib import Path

import pytest
import yaml
from django.conf import settings
from django.core.management import call_command

from explorer.management.commands.daily_housekeeping import TASKS

ROOT = Path(settings.BASE_DIR)


def test_the_tasks_that_had_no_schedule_are_in_the_list():
    names = {name for name, _, _ in TASKS}
    assert "refresh_worklist" in names
    assert "find_repeated_bodies" in names


def test_every_task_is_a_command_that_exists():
    """A name that is not a command fails at 3am and not before."""
    from django.core.management import get_commands

    commands = get_commands()
    for name, _, _ in TASKS:
        assert name in commands, f"{name} is scheduled and is not a command"


def test_every_task_says_why_it_is_there():
    for name, why, _ in TASKS:
        assert why, f"{name} does not say what it is for"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_failing_task_does_not_stop_the_others(monkeypatch, capsys):
    """They touch different things. A worklist count that cannot be
    computed is no reason to skip the boilerplate scan."""
    from explorer.management.commands import daily_housekeeping

    ran = []

    def record(name, *args, **kwargs):
        ran.append(name)
        if name == "first":
            raise RuntimeError("no")

    monkeypatch.setattr(daily_housekeeping, "call_command", record)
    monkeypatch.setattr(
        daily_housekeeping,
        "TASKS",
        (("first", "one", ()), ("second", "two", ())),
    )
    with pytest.raises(SystemExit):
        call_command("daily_housekeeping")
    assert ran == ["first", "second"]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_failure_makes_the_run_fail(monkeypatch):
    """Otherwise the schedule is green and half the work is not done."""
    from explorer.management.commands import daily_housekeeping

    def boom(name, *args, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(daily_housekeeping, "call_command", boom)
    monkeypatch.setattr(daily_housekeeping, "TASKS", (("only", "one", ()),))
    with pytest.raises(SystemExit):
        call_command("daily_housekeeping")


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_whole_list_runs(crawler_schema):
    """Against a real database, not a mock: the point is that these two
    commands can actually run together."""
    call_command("daily_housekeeping")


def test_the_deploy_creates_the_job():
    """A Cloud Run job pins its image when it is created and runs that one
    forever, which is how the scan job went on running an old build. This
    is redeployed with every release."""
    config = yaml.safe_load(
        (ROOT / "gcp/cloudbuild/cloudbuild-datadesk.yaml").read_text()
    )
    step = next(s for s in config["steps"] if s.get("id") == "daily-job")
    script = str(step["args"])
    assert "daily_housekeeping" in script
    assert "-daily" in script
    # It reads the crawler's database: the boilerplate scan groups its
    # articles.
    assert "CRAWLER_DB_USER" in script
