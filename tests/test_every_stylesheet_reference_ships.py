"""Every file a stylesheet or a script points at is in the repo.

The deploy runs `collectstatic` under WhiteNoise's manifest storage, which
refuses a stylesheet that names a missing file. Leaflet's CSS names its
control and marker images; vendoring the CSS without them passed every
test and failed the 2026-11-23 deploy at the Docker build; leaflet.js's
`sourceMappingURL` comment would have failed it next. This checks both
in seconds.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
URL = re.compile(r"""url\(\s*['"]?([^'")]+?)['"]?\s*\)""")
SOURCE_MAP = re.compile(r"^//# sourceMappingURL=(\S+)\s*$", re.M)


def _local(ref):
    """A reference collectstatic resolves on disk: not a data URI, an
    absolute URL, a fragment or an old-IE behaviour."""
    return not re.match(r"(data:|https?:|//|#|/)", ref)


def test_every_url_in_a_stylesheet_names_a_file_that_exists():
    missing = []
    for css in STATIC.rglob("*.css"):
        for ref in URL.findall(css.read_text(encoding="utf-8", errors="replace")):
            if not _local(ref):
                continue
            path = ref.split("?", 1)[0].split("#", 1)[0]
            if not (css.parent / path).resolve().exists():
                missing.append(f"{css.relative_to(STATIC)} -> {ref}")
    assert missing == [], "stylesheets name files not in static/: " + ", ".join(missing)


def test_every_source_map_a_script_names_ships():
    missing = []
    for js in STATIC.rglob("*.js"):
        for ref in SOURCE_MAP.findall(js.read_text(encoding="utf-8", errors="replace")):
            if _local(ref) and not (js.parent / ref).resolve().exists():
                missing.append(f"{js.relative_to(STATIC)} -> {ref}")
    assert missing == [], "scripts name source maps not in static/: " + ", ".join(
        missing
    )
