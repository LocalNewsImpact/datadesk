"""`make fmt` and `make format` both work here.

They did not. This console has `fmt`; the crawler has `format`. Neither
is invoked by CI -- the shared workflow calls `lint`, `typecheck`,
`test` and `test-integration` -- so nothing caught the difference, and
it only bites a person moving between the two.

The failure is quiet, which is what makes it worth a test. The wrong
name did not error: with no such target and nothing else matching, make
exited 0 having done nothing. Run with its output suppressed it reads
exactly like a successful format, and the unformatted commit is found
four minutes later by the pre-push hook.

The crawler carries the mirror of this file.
"""

import re
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


@pytest.fixture(scope="module")
def makefile():
    return MAKEFILE.read_text()


def test_both_names_are_defined(makefile):
    targets = set(re.findall(r"^([a-z][a-z-]*):", makefile, re.M))
    assert {"fmt", "format"} <= targets, sorted(targets)


def test_the_alias_does_not_duplicate_the_recipe(makefile):
    """`format: fmt`, not a second copy of the commands. Two recipes
    drift, and the drift is invisible until one of them is the only one
    somebody runs."""
    line = next(ln for ln in makefile.splitlines() if ln.startswith("format:"))
    assert line.split(":", 1)[1].strip() == "fmt", line


def test_both_are_phony(makefile):
    """A file called `fmt` or `format` would otherwise shadow the target
    and make would decide there was nothing to do."""
    phony = " ".join(ln for ln in makefile.splitlines() if ".PHONY" in ln)
    for name in ("fmt", "format"):
        assert re.search(rf"\b{name}\b", phony), f"{name} is not in .PHONY"


def test_make_resolves_the_alias():
    """Asked of make itself rather than of the text, because the text can
    look right while the target is unreachable."""
    result = subprocess.run(
        ["make", "-n", "format"],
        cwd=MAKEFILE.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    # It must actually run a formatter, not resolve to nothing -- which
    # is precisely what the other name did before.
    assert "black" in result.stdout, result.stdout
