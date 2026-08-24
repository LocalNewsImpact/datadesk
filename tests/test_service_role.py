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
    "DIRECTORY_ADMIN_GATE",
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


def test_the_deploy_resolves_the_directory_to_a_release_tag():
    """The version is resolved when the image is built, not written in
    git -- the same shape the crawler uses, where `kubectl set image`
    pins `${SHORT_SHA}` rather than a hardcoded tag somebody has to
    change by pull request.

    A tag and never a branch: a release happens because somebody bumped
    the version in the directory's own reviewed pull request, so an
    unfinished main cannot walk into this image.

    The bug this catches is silent. If the resolved version stops
    reaching the base image's cache key, a new release does not change
    requirements.txt, the cached base is reused, and the new code never
    installs -- while every step reports success.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    deploy = (root / ".github/workflows/deploy.yml").read_text()
    build = (root / "gcp/cloudbuild/cloudbuild-datadesk.yaml").read_text()
    dockerfile = (root / "Dockerfile.base").read_text()

    # Resolved in the workflow, which holds a token for that repository --
    # from the tag list, newest first, and only release tags.
    assert "repos/LocalNewsImpact/NewsSourceDirectory/tags" in deploy
    assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in deploy
    assert "sort -V" in deploy, "string sort puts v0.9.0 above v0.10.0"

    # Handed to the build as a substitution, the crawler's mechanism for the
    # same problem, and refused there if it did not arrive.
    assert "_DIRECTORY_VERSION=" in deploy
    assert "_DIRECTORY_VERSION" in build
    assert 'if [ -z "${_DIRECTORY_VERSION}" ]; then' in build

    # Carried into the build and into the cache key.
    assert "--build-arg" in build and "DIRECTORY_VERSION" in build
    assert 'cat requirements.txt <(echo "${_DIRECTORY_VERSION}")' in build

    # Installed from that argument, and refused if it is empty.
    assert "ARG DIRECTORY_VERSION" in dockerfile
    assert 'test -n "$DIRECTORY_VERSION"' in dockerfile


def test_requirements_does_not_pin_the_directory():
    """It lived here and cost a pull request in this repository every
    time that package released -- a pull request the ruleset requires
    somebody to approve, which is the manual step moved rather than
    removed."""
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    assert "news-source-directory" not in text


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


def test_the_base_image_lookup_runs_in_a_step_that_has_the_tool():
    """The reuse check decides whether to spend ninety seconds rebuilding
    an image that is already in the registry, and it is a silent decision:
    the command's output goes to /dev/null, so a lookup that always fails
    reports nothing and rebuilds every time. That is what
    `gcloud artifacts docker images describe` did -- the step runs in
    gcr.io/cloud-builders/docker, which has no gcloud.
    """
    from pathlib import Path

    build = (
        Path(__file__).resolve().parent.parent
        / "gcp/cloudbuild/cloudbuild-datadesk.yaml"
    ).read_text()
    base = build[build.index("id: 'base'") : build.index("id: 'app'")]
    assert "docker manifest inspect" in base
    runs = [
        line
        for line in base.splitlines()
        if "gcloud" in line and not line.lstrip().startswith("#")
    ]
    assert runs == [], f"the docker builder has no gcloud: {runs}"


def test_the_build_graph_has_no_cycle():
    """waitFor is a DAG the file does not check for itself, and a cycle
    fails the whole build rather than one step."""
    from pathlib import Path

    import yaml

    spec = yaml.safe_load(
        (
            Path(__file__).resolve().parent.parent
            / "gcp/cloudbuild/cloudbuild-datadesk.yaml"
        ).read_text()
    )
    deps = {s["id"]: (s.get("waitFor") or []) for s in spec["steps"]}
    seen = set()

    def visit(node, path=()):
        assert node not in path, f"cycle: {' -> '.join(path + (node,))}"
        if node in seen:
            return
        for parent in deps[node]:
            visit(parent, path + (node,))
        seen.add(node)

    for node in deps:
        visit(node)


# --- the public data front end (ROADMAP item 24) -----------------------------
#
# `data.localnewsimpact.org` is a hostname anybody may link to. The point of
# it being a separate service rather than a host check on the console is that
# the console does not exist there: not a login form, not a redirect to one,
# not a URL that reverses to one.


def _data_urlconf():
    import importlib

    return importlib.import_module("datadesk.urls_data")


def test_the_data_role_selects_its_own_urlconf():
    import importlib
    import os

    os.environ["SERVICE_ROLE"] = "data"
    try:
        module = importlib.reload(importlib.import_module("datadesk.settings"))
        assert module.ROOT_URLCONF == "datadesk.urls_data"
    finally:
        del os.environ["SERVICE_ROLE"]
        importlib.reload(importlib.import_module("datadesk.settings"))


def test_the_console_is_absent_rather_than_refused():
    """Not 403, not a redirect -- absent. A hostname readers link to should
    have no admin behind it at all."""
    joined = " ".join(_data_routes())
    for forbidden in ("admin", "accounts", "review", "manage", "explorer"):
        assert forbidden not in joined, f"{forbidden} is reachable on the data host"


def _data_routes():
    """Every route the data front end serves, through the namespace."""
    from django.urls import URLResolver

    out = []
    for entry in _data_urlconf().urlpatterns:
        if isinstance(entry, URLResolver):
            out += [str(entry.pattern) + str(p.pattern) for p in entry.url_patterns]
        else:
            out.append(str(entry.pattern))
    return out


def test_it_serves_the_two_things_a_reader_fetches():
    routes = " ".join(_data_routes())
    assert "embed/" in routes
    assert "data.json" in routes


def test_a_published_payload_is_cached_hard_and_the_list_is_not():
    """An immutable version may be cached forever; what names versions may
    not, or a republish never reaches anybody."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "datadesk/urls_data.py"
    ).read_text()
    assert "immutable=True" in source
    assert "max_age=300" in source


def test_the_embed_is_still_framed_by_the_allowlist():
    """Serving it from another host must not loosen who may frame it: the
    view is the console's own rather than a copy that could drift."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "datadesk/urls_data.py"
    ).read_text()
    assert "visuals.embed" in source
    assert "from visuals import views as visuals" in source


def test_every_front_end_checks_it_is_reachable():
    """gcloud reports a refused IAM binding as a warning and exits zero, so
    a deploy reports success while every request gets a 403 before reaching
    Django. The directory's first deploy did that; so did the data front
    end's, because it was added without the check the other two carry.

    Every service that takes public traffic needs one, and a new one added
    without it should fail here rather than in production.
    """
    import re
    from pathlib import Path

    import yaml

    spec = yaml.safe_load(
        (
            Path(__file__).resolve().parent.parent
            / "gcp/cloudbuild/cloudbuild-datadesk.yaml"
        ).read_text()
    )
    steps = {s["id"]: " ".join(s.get("args", [])) for s in spec["steps"]}

    def names(pattern):
        """Service names, however the config quotes or substitutes them."""
        found = set()
        for body in steps.values():
            found |= set(re.findall(pattern, body))
        return {s.strip('"').replace("${_SERVICE}", "datadesk") for s in found}

    deployed = names(r"gcloud run deploy (\S+)")
    checked = names(r"get-iam-policy (\S+)")

    missing = deployed - checked
    assert not missing, (
        f"services deployed with no invoker check: {sorted(missing)}. "
        "A refused binding would deploy green and 403 every request."
    )


def test_the_data_front_end_can_reverse_what_its_templates_ask_for():
    """The renderers reverse `visuals:data` to find their own feed. The
    data urlconf declared its routes bare, so the embed raised
    NoReverseMatch and returned 500 in production -- while `_health`
    passed, because it reverses nothing.

    A route is not exercised by the route beside it.
    """
    import importlib

    from django.urls import URLResolver

    module = importlib.reload(importlib.import_module("datadesk.urls_data"))
    namespaces = {
        entry.namespace
        for entry in module.urlpatterns
        if isinstance(entry, URLResolver)
    }
    assert "visuals" in namespaces


def test_the_renderers_all_reverse_the_same_namespace():
    """A renderer added later that reverses something else would 500 the
    same way, and only on the data host."""
    import re
    from pathlib import Path

    renderers = Path(__file__).resolve().parent.parent / "templates/visuals/renderers"
    wanted = set()
    for template in renderers.glob("*.html"):
        wanted |= set(re.findall(r"{%\s*url\s+'([a-z_]+):", template.read_text()))
    assert wanted <= {"visuals"}, f"renderers reverse {sorted(wanted)}"


# --- the two failures a reader actually hit ----------------------------------


def test_the_snippets_fallback_link_resolves_on_the_host_it_names():
    """The placeholder in every snippet links to /visuals/<slug>/ on the
    data host, for the reader whose browser never ran the script. That URL
    was declared only on the console, so the link 404'd on the one host it
    was ever pointed at."""
    from visuals.embed import EMBED_HOST, snippet

    class _V:
        slug = "boone-county-coverage"
        title = "Where Boone County reports"

    linked = snippet(_V())
    assert f'href="https://{EMBED_HOST}/visuals/{_V.slug}/"' in linked

    routes = _data_routes()
    assert (
        "visuals/<slug:slug>/" in routes
    ), "the snippet's fallback link has no route on the data host"


def test_the_public_page_is_not_the_walled_one():
    """`page` raises 404 for anyone not signed in with a role. Wiring it to
    a host that has no sign-in would 404 every reader."""
    from django.urls import resolve

    from visuals import views

    assert views.public_page is not views.page

    match = resolve("/visuals/boone-county-coverage/", urlconf=_data_urlconf())
    # Cached views are wrapped, so compare the name `functools.wraps` kept.
    assert (
        match.func.__name__ == "public_page"
    ), f"the data host routes the visual page to {match.func.__name__}"


def test_a_visual_may_be_framed_by_somebody_elses_site():
    """`'self'` as the default meant the only site allowed to frame an
    embed was the one serving it, so every pasted snippet showed a browser
    security refusal instead of a chart."""
    from visuals.models import Visual

    default = Visual._meta.get_field("frame_ancestors").get_default()
    assert default == "*", (
        f"frame_ancestors defaults to {default!r}; an embed nobody may "
        "frame is not an embed"
    )


def test_a_bad_image_still_cannot_reach_a_second_console_untested():
    """The directory's traffic shift waits on Datadesk's, so an image that
    breaks the console is caught before anything else serves it.

    That guard used to sit on `sources-migrate`, four steps earlier, on a
    step that moves no traffic at all -- which bought the same safety and
    charged the whole directory chain for it. Moving it must not have
    dropped it.
    """
    from pathlib import Path

    import yaml

    spec = yaml.safe_load(
        (
            Path(__file__).resolve().parent.parent
            / "gcp/cloudbuild/cloudbuild-datadesk.yaml"
        ).read_text()
    )
    waits = {s["id"]: s.get("waitFor", []) for s in spec["steps"]}

    assert "shift" in waits["sources-shift"]
    assert "sources-prove" in waits["sources-shift"]
    assert "shift" in waits["data-candidate"]


def test_warming_the_cache_does_not_sit_on_the_end_of_the_build():
    """It depends on the migration -- warming reads through the ORM -- and
    on nothing after it. Chained behind the traffic shift it added its own
    cold start to the end of every deploy, and the reason given for that
    (priming "the revision being replaced") does not describe a database
    cache, which every revision shares."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parent.parent
    spec = yaml.safe_load(
        (root / "gcp/cloudbuild/cloudbuild-datadesk.yaml").read_text()
    )
    waits = {s["id"]: s.get("waitFor", []) for s in spec["steps"]}
    assert waits["warm-job"] == ["migrate"]

    settings = (root / "datadesk/settings.py").read_text()
    assert (
        "DatabaseCache" in settings
    ), "a per-process cache would make the warm job revision-specific again"

    # Fire and forget: waiting on the execution would block the rollout by
    # the two and a half minutes the warm actually takes.
    warm = next(s for s in spec["steps"] if s["id"] == "warm-job")
    body = " ".join(warm["args"])
    execute = body.split("jobs execute", 1)[1]
    assert "--wait" not in execute
