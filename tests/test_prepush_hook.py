"""The pre-push hook must run what CI runs, and must actually block.

A lint failure reached GitHub on PR #204: black was run locally, ruff
was not, and a bare expression statement went out as a red pull request.
The hook exists so that cannot happen again -- which is only true if the
hook works, so it is tested rather than assumed.

The equivalent hook in the crawler repository shipped with a bug that
made it refuse every push (a variable read one line above its
assignment). These tests run the installed hook in a scratch repository
rather than reading it.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "scripts" / "setup-hooks.sh"


def _run(command, cwd, env=None):
    # Git exports GIT_DIR to a hook when the push comes from a linked
    # worktree (from the primary checkout it does not). These tests run
    # inside the hook, so with that variable inherited the scratch
    # `git init` below re-initialised the real repository as bare, and
    # `git config user.email t@e` wrote into its config. Every GIT_*
    # variable goes, so the scratch repository is the one at `cwd`.
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        env={**clean, **(env or {})},
    )


def test_the_installer_is_valid_shell():
    assert _run(f"bash -n {INSTALLER}", REPO).returncode == 0


def test_the_installed_hook_is_valid_shell(tmp_path):
    hook = _install_into(tmp_path)
    assert _run(f"bash -n {hook}", tmp_path).returncode == 0


def test_the_scratch_repository_is_not_the_one_git_exported(tmp_path, monkeypatch):
    """Pushed from a linked worktree, git runs the hook with GIT_DIR set to
    the pushing repository. The first time this suite ran inside such a
    hook, the scratch `git init` re-initialised that repository as bare
    and the test identity landed in its config."""
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    assert _run("git init -q", decoy).returncode == 0
    before = (decoy / ".git" / "config").read_text()
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))

    hook = _install_into(tmp_path)

    assert hook.exists(), "the hook was installed somewhere other than the scratch repo"
    assert (decoy / ".git" / "config").read_text() == before
    assert not (decoy / ".git" / "hooks" / "pre-push").exists()


def _install_into(tmp_path):
    """A scratch git repo with the hook installed, and a fake `make`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run("git init -q && git config user.email t@e && git config user.name t", repo)
    (repo / "scripts").mkdir()
    shutil.copy(INSTALLER, repo / "scripts" / "setup-hooks.sh")
    os.chmod(repo / "scripts" / "setup-hooks.sh", 0o755)
    result = _run("./scripts/setup-hooks.sh", repo)
    assert result.returncode == 0, result.stderr
    return repo / ".git" / "hooks" / "pre-push"


def _fake_make(repo, exit_code):
    """A `make` on PATH that records its arguments and exits as told."""
    binn = repo / "fakebin"
    binn.mkdir(exist_ok=True)
    make = binn / "make"
    make.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "make called with: $*" >> "{repo}/make.log"\n'
        f"exit {exit_code}\n"
    )
    os.chmod(make, 0o755)
    return {"PATH": f"{binn}:{os.environ['PATH']}"}


def test_the_hook_runs_make_check(tmp_path):
    hook = _install_into(tmp_path)
    repo = hook.parent.parent.parent
    env = _fake_make(repo, 0)
    result = _run(str(hook), repo, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "make called with: check" in (repo / "make.log").read_text()


def test_the_hook_blocks_the_push_when_checks_fail(tmp_path):
    """The whole point. A hook that reports failure and exits 0 is worse
    than no hook, because it is trusted."""
    hook = _install_into(tmp_path)
    repo = hook.parent.parent.parent
    env = _fake_make(repo, 1)
    result = _run(str(hook), repo, env)
    assert result.returncode == 1
    assert "push aborted" in (result.stdout + result.stderr).lower()


def test_the_hook_does_not_read_a_variable_before_assigning_it():
    """The exact defect that made the crawler's hook refuse every push."""
    body = (REPO / "scripts" / "setup-hooks.sh").read_text()
    start = body.index("HOOK_BODY'")
    hook_body = body[start : body.index("\nHOOK_BODY\n", start)]
    assigned = hook_body.index("REPO_ROOT=")
    first_use = hook_body.index('"$REPO_ROOT"')
    assert assigned < first_use


@pytest.mark.parametrize(
    "command", ["ruff", "black", "isort", "mypy", "makemigrations", "pytest"]
)
def test_make_check_still_covers_every_ci_step(command):
    """The hook delegates to `make check`; this asserts what that means.

    If a step is added to CI and not to the Makefile, the hook silently
    stops matching CI and this fails.
    """
    makefile = (REPO / "Makefile").read_text()
    assert command in makefile


def test_the_ci_workflow_adds_no_step_the_makefile_lacks():
    workflow = (REPO / ".github/workflows/ci.yml").read_text()
    makefile = (REPO / "Makefile").read_text()
    for line in workflow.splitlines():
        stripped = line.strip()
        if not stripped.startswith("run: "):
            continue
        command = stripped[len("run: ") :].strip()
        if command.startswith("pip install") or command.startswith("python -m pip"):
            continue
        head = command.split()[0]
        if head in {"python", "ruff", "black", "isort", "mypy"}:
            # The distinguishing word, not the interpreter.
            token = command.split()[1] if head == "python" else head
            token = token.lstrip("-")
            assert token in makefile, f"CI runs `{command}`, the Makefile does not"


def test_no_workflow_names_the_retired_database_instance():
    """A runtime change to a Cloud Run service does not survive a deploy.

    The instance was migrated from PD_HDD to PD_SSD, which Cloud SQL can
    only do by building a new instance under a new name. Both services
    were pointed at it with `gcloud run services update` -- and the very
    next deploy put them back, because the connection name is written in
    the workflow and in the Cloud Build substitutions. The old instance
    picked up fresh connections minutes after it had supposedly been
    retired.

    The deployed configuration is the one in the repository. This asserts
    it names no instance that is going away.
    """
    from pathlib import Path

    retired = "mizzou-db-prod"
    for pattern in (".github/workflows/*.yml", "gcp/cloudbuild/*.yaml"):
        for path in Path(REPO).glob(pattern):
            body = path.read_text()
            for line in body.splitlines():
                if retired in line and f"{retired}-ssd" not in line:
                    raise AssertionError(
                        f"{path.name} still names {retired}: {line.strip()}"
                    )
