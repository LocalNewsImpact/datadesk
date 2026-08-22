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
    config = build_socialaccount_providers("", "", ["example.org"])
    assert "APP" not in config["google"]
    config = build_socialaccount_providers("id", "secret", ["example.org"])
    assert config["google"]["APP"]["client_id"] == "id"


def test_hd_hint_only_for_single_domain():
    single = build_socialaccount_providers("", "", ["example.org"])
    assert single["google"]["AUTH_PARAMS"] == {"hd": "example.org"}
    multi = build_socialaccount_providers("", "", ["a.org", "b.org"])
    assert multi["google"]["AUTH_PARAMS"] == {}
    none = build_socialaccount_providers("", "", [])
    assert none["google"]["AUTH_PARAMS"] == {}


def test_forwarded_proto_is_trusted():
    """Cloud Run forwards plain HTTP; without this Django builds http://
    absolute URLs and the OAuth callback fails redirect_uri_mismatch."""
    from django.conf import settings
    from django.test import RequestFactory

    assert settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    request = RequestFactory().get("/", HTTP_X_FORWARDED_PROTO="https")
    assert request.is_secure()
    assert request.build_absolute_uri("/x/").startswith("https://")
