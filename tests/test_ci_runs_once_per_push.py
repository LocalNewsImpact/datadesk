"""One commit, one CI run.

`on: push:` with no branch list runs the whole workflow on every branch,
and a pull request already runs it on the merge ref -- so each push to a
pull request branch ran lint, mypy, a Postgres service and 1,388 tests
twice. `concurrency` does not collapse them: the group is the ref, and
the two runs have different refs (refs/heads/... against
refs/pull/N/merge). PR #247 did it, and so had every pull request
before it.

The crawler has always scoped its push trigger to main. This asserts
that this repository does too, and that nothing quietly widens it back.

It replaces an assertion that the trigger stay unscoped, whose reason was
that a contributor sees failures before opening a pull request. That
feedback arrives earlier from the pre-push hook -- the same `make check`,
on the same commit, before it leaves the machine, and `conforms.yml`
fails the build if the hook goes missing. What is given up is CI on a
branch pushed with no pull request open, by someone with write access
here; a fork's push never ran these workflows anyway.
"""

from pathlib import Path

import pytest
import yaml
from django.conf import settings

WORKFLOWS = Path(settings.BASE_DIR) / ".github/workflows"


def _triggers(name):
    # `on` is YAML 1.1's boolean true, which is why this reads it by that
    # key rather than the string.
    return yaml.safe_load((WORKFLOWS / name).read_text())[True]


def test_ci_runs_on_pushes_to_main_only():
    push = _triggers("ci.yml")["push"]
    assert push is not None, "an unscoped push: runs on every branch"
    assert push["branches"] == ["main"]


def test_ci_still_runs_on_pull_requests():
    """The push trigger is narrowed, not the coverage: a pull request is
    where a branch gets checked."""
    assert "pull_request" in _triggers("ci.yml")


@pytest.mark.parametrize("workflow", ["ci.yml", "deploy.yml"])
def test_no_workflow_pushes_from_every_branch(workflow):
    """Deploy was already scoped. A new workflow copied from the old CI
    would not be."""
    triggers = _triggers(workflow)
    if "push" not in triggers:
        return
    push = triggers["push"]
    # `push:` with nothing under it parses as None, which is the bug
    # itself -- not "no push trigger". Reading it with .get() and
    # returning early on None is how this test would have passed while
    # the workflow ran on every branch.
    assert push is not None, f"{workflow} has a bare push:, so every branch runs it"
    assert push.get("branches") == ["main"], f"{workflow} triggers on every branch"
