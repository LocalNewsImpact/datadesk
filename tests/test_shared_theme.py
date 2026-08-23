"""The console theme is shared with another service, so it has to hold
still.

The Source Directory is its own repository and its own deployment. What
joins the two is `static/css/tokens.css`, which it links across the
origin, and `static/css/django-admin.css`, which maps those tokens onto
the Django admin's own variables. Neither file can be checked by that
repository's tests, so the checks live here: a token renamed or dropped
would silently unstyle the other console, and nothing else would notice.
"""

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent / "static" / "css"
TOKENS = CSS / "tokens.css"
CONSOLE = CSS / "datadesk.css"
ADMIN_BRIDGE = CSS / "django-admin.css"

# Variables Django's admin defines itself. The bridge assigns to these;
# it must not read one as if the console provided it.
DJANGO_ADMIN_VARS = re.compile(
    r"^--(primary|secondary|accent|body|header|breadcrumbs|link|hairline|"
    r"border-color|error|message|darkened|selected|button|default-button|"
    r"close-button|delete-button|object-tools|font-family)"
)


def _defined(path):
    """Custom properties a stylesheet defines."""
    return set(re.findall(r"^\s*(--[a-z0-9-]+)\s*:", path.read_text(), re.M))


def _used(path):
    """Custom properties a stylesheet reads through var()."""
    return set(re.findall(r"var\(\s*(--[a-z0-9-]+)", path.read_text()))


def test_the_tokens_file_defines_the_shared_values():
    tokens = _defined(TOKENS)
    assert "--accent" in tokens
    assert "--text" in tokens
    assert "--surface" in tokens
    assert len(tokens) > 40, "the token set looks truncated"


def test_the_console_reads_every_token_from_the_shared_file():
    """datadesk.css must not redefine a shared value: two definitions
    drift, and the other console only ever sees one of them."""
    redefined = _defined(CONSOLE) & _defined(TOKENS)
    assert redefined == set(), f"defined in both files: {sorted(redefined)}"


# Set per-element by markup rather than by a stylesheet: the confidence
# bar's fill fraction. It carries its own fallback.
PER_ELEMENT = {"--v"}


def test_the_console_uses_no_token_the_shared_file_lacks():
    """A var() naming nothing is invalid at computed-value time, so the
    property silently falls back to inherited — text that looks almost
    right and is not. This is the check that catches it."""
    missing = _used(CONSOLE) - _defined(TOKENS) - _defined(CONSOLE) - PER_ELEMENT
    assert missing == set(), f"datadesk.css reads undefined tokens: {sorted(missing)}"


def test_the_admin_bridge_only_reads_shared_tokens():
    """The bridge runs in the other service, where datadesk.css is not
    loaded. Every value it reads has to come from tokens.css."""
    reads = {v for v in _used(ADMIN_BRIDGE) if not DJANGO_ADMIN_VARS.match(v)}
    missing = reads - _defined(TOKENS)
    assert missing == set(), f"bridge reads non-shared tokens: {sorted(missing)}"


def test_the_admin_bridge_leaves_the_public_widget_alone():
    """The embeddable directory widget matches localnewsimpact.org and
    must keep doing so. The bridge is for the signed-in backend."""
    text = ADMIN_BRIDGE.read_text()
    assert "widget" not in text.lower() or "must keep doing so" in text
    for selector in (".lnic-directory", ".nsd-widget", "#directory"):
        assert selector not in text


@pytest.mark.parametrize("path", [TOKENS, ADMIN_BRIDGE])
def test_the_shared_files_are_collected_as_static(path, settings):
    """They are only shareable if WhiteNoise serves them; a file outside
    the static tree would 404 for the other console."""
    assert path.exists()
    assert str(path).startswith(str(Path(settings.BASE_DIR) / "static"))
