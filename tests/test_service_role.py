"""One image, several front ends (ROADMAP.md item 14).

`SERVICE_ROLE` says which front end a process serves. The default is
Datadesk's own console; `sources` serves the Source Directory from the
same image, with the `directory` package installed as an app.

The directory is loaded only for its own front end rather than
unconditionally. It registers eleven models against the default
`AdminSite` and patches `admin.site.index` at import, so loading it for
Datadesk would put its models and its dashboard on Datadesk's admin.
That merge is wanted eventually — the suite shares one set of admins —
but it needs the per-application grants from item 1 to filter what each
person sees.

The failure these guard against is quiet. `ROOT_URLCONF` and
`LOGIN_REDIRECT_URL` both have defaults further down `settings.py`, so a
conditional assignment placed before them is silently replaced and the
wrong front end is served with no error at all.
"""

import importlib

# A snapshot rather than the module: importlib.reload mutates the module
# in place and returns the same object, so restoring the environment
# afterwards would rewrite the very values under test.
WATCHED = (
    "SERVICE_ROLE",
    "ROOT_URLCONF",
    "LOGIN_REDIRECT_URL",
    "INSTALLED_APPS",
    "MIDDLEWARE",
    "SITE_ID",
)


def _settings(role=None):
    """Settings as they resolve under a given SERVICE_ROLE."""
    import os
    from types import SimpleNamespace

    previous = os.environ.get("SERVICE_ROLE")
    if role is None:
        os.environ.pop("SERVICE_ROLE", None)
    else:
        os.environ["SERVICE_ROLE"] = role
    try:
        module = importlib.reload(importlib.import_module("datadesk.settings"))
        return SimpleNamespace(**{k: getattr(module, k) for k in WATCHED})
    finally:
        if previous is None:
            os.environ.pop("SERVICE_ROLE", None)
        else:
            os.environ["SERVICE_ROLE"] = previous
        importlib.reload(importlib.import_module("datadesk.settings"))


def test_the_default_is_datadesks_own_console():
    """An unset variable must serve Datadesk, not something else."""
    s = _settings(None)
    assert s.SERVICE_ROLE == "datadesk"
    assert s.ROOT_URLCONF == "datadesk.urls"
    assert s.LOGIN_REDIRECT_URL == "/"


def test_datadesk_does_not_load_the_directory():
    s = _settings("datadesk")
    assert "directory" not in s.INSTALLED_APPS
    assert "import_export" not in s.INSTALLED_APPS
    assert "simple_history" not in s.INSTALLED_APPS


def test_the_sources_role_loads_the_directory_first():
    """First in the list, because it overrides templates belonging to
    django.contrib.admin and to allauth, and APP_DIRS takes the first
    match walking INSTALLED_APPS."""
    s = _settings("sources")
    assert s.INSTALLED_APPS[0] == "directory"
    assert "import_export" in s.INSTALLED_APPS
    assert "simple_history" in s.INSTALLED_APPS


def test_the_sources_role_serves_its_own_urls():
    """The bug this catches: both settings have defaults later in the
    file, so a conditional assignment before them is overwritten and the
    console is served on the directory's hostname."""
    s = _settings("sources")
    assert s.ROOT_URLCONF == "datadesk.urls_sources"
    assert s.LOGIN_REDIRECT_URL == "/admin/"


def test_history_middleware_only_where_its_app_is_loaded():
    """simple_history's middleware reads a model the app installs. In a
    process without it the middleware is dead weight at best."""
    assert not any("simple_history" in m for m in _settings("datadesk").MIDDLEWARE)
    assert any("simple_history" in m for m in _settings("sources").MIDDLEWARE)


def test_an_unknown_role_falls_back_to_the_console():
    """A typo in a deployment variable should not produce a process that
    serves nothing recognisable."""
    s = _settings("nonsense")
    assert s.ROOT_URLCONF == "datadesk.urls"
    assert "directory" not in s.INSTALLED_APPS


def test_the_directory_package_is_pinned_to_a_tag():
    """A branch would make the build unreproducible: two deploys of the
    same commit could install different code."""
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    line = next(x for x in text.splitlines() if "news-source-directory" in x)
    assert "github.com/LocalNewsImpact/NewsSourceDirectory" in line
    ref = line.rsplit("@", 1)[1].strip()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", ref), f"not a version tag: {ref}"


# --- what the sources deployment must also supply ---------------------------


def _postgres_options(**env):
    """The default connection's OPTIONS under a given environment."""
    import importlib
    import os

    previous = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items() if v is not None})
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        module = importlib.reload(importlib.import_module("datadesk.settings"))
        return dict(module.DATABASES["default"].get("OPTIONS", {}))
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(importlib.import_module("datadesk.settings"))


def test_a_search_path_reaches_the_connection():
    """The directory's tables live in a `directory` schema of this
    database. Without the search path they are simply not visible, and
    every query against them fails as a missing relation."""
    options = _postgres_options(
        CLOUD_SQL_CONNECTION_NAME="p:r:i",
        DB_PASSWORD="x",
        DB_SEARCH_PATH="directory,public",
    )
    assert options["options"] == "-c search_path=directory,public"


def test_no_search_path_leaves_the_connection_alone():
    options = _postgres_options(
        CLOUD_SQL_CONNECTION_NAME="p:r:i", DB_PASSWORD="x", DB_SEARCH_PATH=None
    )
    assert "options" not in options
    assert options["connect_timeout"] == 10


def test_the_site_row_is_a_deployment_choice():
    """django_site is shared with the directory and a row cannot be.
    This console owns row 1; the directory owns row 2."""
    import importlib
    import os

    assert _settings("datadesk").SITE_ID == 1
    os.environ["SITE_ID"] = "2"
    try:
        module = importlib.reload(importlib.import_module("datadesk.settings"))
        assert module.SITE_ID == 2
    finally:
        os.environ.pop("SITE_ID", None)
        importlib.reload(importlib.import_module("datadesk.settings"))
