"""A dependency the image does not install is a 500 on one page.

`review/dispositions.py` imports `lnic_contracts` at module scope, and
`review/views.py` imports dispositions inside the request rather than at
module scope. So a missing package does not stop the service starting,
does not fail collectstatic, does not fail a health check, and does not
fail any query -- it fails exactly one page, at the moment somebody opens
it.

That happened. The package was listed in requirements-dev.txt, which a
checkout and CI install, and not in requirements.txt, which the image
installs. Every test passed, a reproduction against the production
databases returned 200, and `/review/queue/` raised ModuleNotFoundError
in production.

Nothing in a test run can notice: the test environment installs
requirements-dev.txt. So the check is on the file.
"""

from pathlib import Path

from django.conf import settings

RUNTIME = Path(settings.BASE_DIR) / "requirements.txt"
DEV = Path(settings.BASE_DIR) / "requirements-dev.txt"


def _requirement(text, name):
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line.split()[0] == name:
            return line
    return None


def test_the_contract_is_a_runtime_dependency():
    """The queue view cannot render without it, so it belongs in the file
    the image installs."""
    assert _requirement(RUNTIME.read_text(), "lnic-contracts"), (
        "lnic-contracts is not in requirements.txt; the deployed image "
        "will not have it and /review/queue/ will 500"
    )


def test_it_is_not_only_a_development_dependency():
    """Where it was. requirements-dev.txt includes requirements.txt, so a
    line here as well would only be a second pin to keep in step."""
    assert not _requirement(DEV.read_text(), "lnic-contracts")


def test_every_module_the_smoke_check_imports_exists():
    """The deploy's import check names modules by string. A rename that
    left one behind would make the check pass by not testing anything."""
    import importlib

    from explorer.management.commands.smoke_queries import DEFERRED_IMPORTS

    for name in DEFERRED_IMPORTS:
        importlib.import_module(name)


def test_the_module_that_broke_is_covered_by_the_import_check():
    from explorer.management.commands.smoke_queries import DEFERRED_IMPORTS

    assert "review.dispositions" in DEFERRED_IMPORTS
