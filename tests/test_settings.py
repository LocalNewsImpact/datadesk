"""Settings module sanity: it imports and the env seams behave."""

from datadesk.settings import build_socialaccount_providers, env_bool, env_list


def test_settings_module_imports():
    from datadesk import settings

    assert settings.SECRET_KEY
    assert settings.ROOT_URLCONF == "datadesk.urls"


def test_env_bool_defaults(monkeypatch):
    monkeypatch.delenv("X_FLAG", raising=False)
    assert env_bool("X_FLAG") is False
    assert env_bool("X_FLAG", default=True) is True
    monkeypatch.setenv("X_FLAG", "true")
    assert env_bool("X_FLAG") is True
    monkeypatch.setenv("X_FLAG", "0")
    assert env_bool("X_FLAG") is False


def test_env_list_parses_and_strips(monkeypatch):
    monkeypatch.setenv("X_LIST", "a.example, b.example ,")
    assert env_list("X_LIST") == ["a.example", "b.example"]
    monkeypatch.delenv("X_LIST", raising=False)
    assert env_list("X_LIST") == []


def test_provider_config_omits_app_without_credentials():
    # An APP entry with blank credentials produces a broken login flow;
    # the provider must be described without one (NewsSourceDirectory
    # pattern).
    config = build_socialaccount_providers("", "")
    assert "APP" not in config["google"]
    config = build_socialaccount_providers("id", "secret")
    assert config["google"]["APP"]["client_id"] == "id"


def test_no_hosted_domain_is_sent_to_google():
    """`hd` locks the email field on Google's own sign-in page to one
    Workspace domain. An invited colleague at another university cannot type
    their address into it -- the flow ends before any code here runs, so the
    adapter that would have admitted them by invitation never sees them.
    Whatever ALLOWED_AUTH_DOMAINS says, the chooser stays open and the
    adapter stays the only gate."""
    config = build_socialaccount_providers("id", "secret")
    assert config["google"]["AUTH_PARAMS"] == {}

    import inspect

    from datadesk import settings

    source = inspect.getsource(settings.build_socialaccount_providers)
    assert '"hd"' not in source, "the account chooser is restricted again"


def test_forwarded_proto_is_trusted():
    """Cloud Run forwards plain HTTP; without this Django builds http://
    absolute URLs and the OAuth callback fails redirect_uri_mismatch."""
    from django.conf import settings
    from django.test import RequestFactory

    assert settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    request = RequestFactory().get("/", HTTP_X_FORWARDED_PROTO="https")
    assert request.is_secure()
    assert request.build_absolute_uri("/x/").startswith("https://")


# --- SCOPE.md cross-references ---------------------------------------------
#
# Inserting §2.3 (the extraction review queue) shifted import/export,
# dataset management, cost and visuals down one each, and every docstring
# still cited the old numbers. These pin the sections a reader is sent to.


def _scope_headings():
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "SCOPE.md").read_text()
    return {
        f"§{number}": title.strip()
        for number, title in re.findall(r"^### (2\.\d+) (.+)$", text, re.MULTILINE)
    }


def test_every_section_a_module_cites_exists():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    headings = _scope_headings()
    missing = set()
    for path in root.glob("*/**/*.py"):
        if ".venv" in path.parts or "migrations" in path.parts:
            continue
        for cited in re.findall(r"§2\.\d+", path.read_text()):
            if cited not in headings:
                missing.add(f"{path.relative_to(root)} cites {cited}")
    assert missing == set()


def test_the_sections_modules_cite_are_the_ones_they_implement():
    headings = _scope_headings()
    assert headings["§2.3"] == "Extraction review queue"
    assert headings["§2.4"] == "Import and export"
    assert headings["§2.5"] == "Dataset creation and maintenance"
    assert headings["§2.6"] == "Cost insight"
    assert headings["§2.7"] == "Visuals platform"

    import datasets.places
    import explorer.costs
    import review.imports
    import review.queue
    import visuals.services

    assert "§2.3" in review.queue.__doc__
    assert "§2.4" in review.imports.__doc__
    assert "§2.5" in datasets.places.__doc__
    assert "§2.6" in explorer.costs.__doc__
    assert "§2.7" in visuals.services.__doc__
