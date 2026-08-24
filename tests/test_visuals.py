"""The visuals platform: registry, snapshots, publishing, the feed, and
the embed with its stability rule."""

from unittest import mock

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry
from visuals.models import Visual
from visuals.services import publish, refresh_snapshot, unpublish

pytestmark = pytest.mark.django_db

ROWS_V1 = [{"county": "Boone", "stories": 41}]
ROWS_V2 = [{"county": "Boone", "stories": 55}]


@pytest.fixture
def author(django_user_model):
    return django_user_model.objects.create_user(
        "author", email="author@localnewsimpact.org"
    )


@pytest.fixture
def visual(author):
    return Visual.objects.create(
        slug="story-geography",
        title="Story geography",
        source_kind="bigquery",
        query="SELECT county, stories FROM x",
        template="table",
        created_by=author,
    )


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="viewer")
    client.force_login(user)
    return user


def _snapshot(visual, author, rows):
    with mock.patch("explorer.analytics.query_rows", return_value=rows):
        return refresh_snapshot(visual, author)


# --- snapshots and publishing -----------------------------------------------


def test_snapshots_version_up_and_audit(visual, author):
    s1 = _snapshot(visual, author, ROWS_V1)
    s2 = _snapshot(visual, author, ROWS_V2)
    assert (s1.version, s2.version) == (1, 2)
    assert AuditLogEntry.objects.filter(action="visual:snapshot").count() == 2


def test_publish_pins_the_latest_snapshot(visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    visual.refresh_from_db()
    assert visual.status == Visual.PUBLISHED
    assert visual.pinned_snapshot.version == 1
    assert AuditLogEntry.objects.filter(action="visual:publish").exists()


def test_publish_without_a_snapshot_takes_one(visual, author):
    with mock.patch("explorer.analytics.query_rows", return_value=ROWS_V1):
        publish(visual, author)
    visual.refresh_from_db()
    assert visual.pinned_snapshot.version == 1


# --- the embed stability rule ------------------------------------------------


def test_embed_serves_the_pin_while_data_moves_on(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    _snapshot(visual, author, ROWS_V2)  # nightly sync moved the data

    feed = client.get("/visuals/story-geography/data.json").json()
    assert feed["version"] == 1
    assert feed["data"] == ROWS_V1  # the pinned truth, not the new rows


def test_live_requires_explicit_opt_in(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)

    feed = client.get("/visuals/story-geography/data.json?live=1").json()
    assert feed["data"] == ROWS_V1  # allow_live off: ?live=1 is ignored

    Visual.objects.filter(pk=visual.pk).update(allow_live=True)
    with mock.patch("explorer.analytics.query_rows", return_value=ROWS_V2):
        feed = client.get("/visuals/story-geography/data.json?live=1").json()
    assert feed["data"] == ROWS_V2
    assert feed["version"] is None


# --- the public surface (SCOPE.md §3) ----------------------------------------


def test_published_embed_and_feed_are_public(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    embed = client.get("/embed/story-geography/")
    assert embed.status_code == 200
    # Any site, because that is what publishing a visual is for. This read
    # `'self'` and passed, while every snippet pasted into an article showed
    # a browser security refusal -- the only site permitted to frame the
    # embed was the one already serving it.
    assert embed["Content-Security-Policy"] == "frame-ancestors *"
    assert "X-Frame-Options" not in embed
    assert client.get("/visuals/story-geography/data.json").status_code == 200


@pytest.mark.urls("datadesk.urls_data")
def test_the_data_host_serves_the_page_the_snippet_links_to(client, visual, author):
    """A reader whose browser never ran the embed script follows the link
    in the placeholder. It points at /visuals/<slug>/ on the data host,
    where nothing was listening -- so the fallback was a 404."""
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)

    # The slug address is the one already pasted into articles; it now
    # redirects, permanently, to the one that cannot move.
    hop = client.get("/visuals/story-geography/")
    assert hop.status_code == 301
    assert hop["Location"] == f"/visuals/{visual.uuid}/"

    page = client.get(hop["Location"])
    assert page.status_code == 200
    body = page.content.decode()
    assert visual.title in body
    # No console on this host: not a nav to one, not a sign-in.
    assert "/accounts/" not in body
    # And the snippet is here, because a newsroom that found the visual
    # this way is exactly who wants to embed it -- but folded away. A
    # reader came for the chart, not for the plumbing.
    assert "datadesk-embed.js" in body
    assert "<details>" in body and "<details open" not in body
    snippet_at = body.index("datadesk-embed.js")
    assert (
        body.index("<details>") < snippet_at
    ), "the embed code must sit inside the disclosure, not above it"


@pytest.mark.urls("datadesk.urls_data")
def test_the_data_host_does_not_leak_a_draft(client, visual):
    """Nobody signs in here, so a draft has no audience it could be
    previewed for -- and the slug redirect must not answer either. It
    would 404 at the far end while confirming on the way that the visual
    exists, and handing out its uuid.
    """
    assert client.get(f"/visuals/{visual.uuid}/").status_code == 404
    assert client.get("/visuals/story-geography/").status_code == 404


def test_frame_ancestors_come_from_the_visual(client, visual, author):
    Visual.objects.filter(pk=visual.pk).update(
        frame_ancestors="'self' https://www.localnewsimpact.org"
    )
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    visual.refresh_from_db()
    embed = client.get("/embed/story-geography/")
    assert (
        embed["Content-Security-Policy"]
        == "frame-ancestors 'self' https://www.localnewsimpact.org"
    )


def test_drafts_are_absent_for_the_public(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    assert client.get("/embed/story-geography/").status_code == 404
    assert client.get("/visuals/story-geography/data.json").status_code == 404


def test_drafts_preview_for_signed_in_roles(client, viewer, visual, author):
    _snapshot(visual, author, ROWS_V1)
    assert client.get("/embed/story-geography/").status_code == 200
    feed = client.get("/visuals/story-geography/data.json").json()
    assert feed["version"] == 1  # latest snapshot stands in for the pin


def test_full_page_stays_behind_the_sign_in_wall(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    assert client.get("/visuals/story-geography/").status_code == 404


def test_full_page_renders_for_a_viewer(client, viewer, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    response = client.get("/visuals/story-geography/")
    assert response.status_code == 200
    assert "Story geography" in response.content.decode()
    assert "Pinned at snapshot v1" in response.content.decode()


def test_unpublish_returns_to_draft(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    unpublish(visual, author)
    assert client.get("/embed/story-geography/").status_code == 404


def test_unknown_renderer_is_a_validation_error(author):
    from django.core.exceptions import ValidationError

    visual = Visual(
        slug="x",
        title="X",
        source_kind="bigquery",
        query="SELECT 1",
        template="does-not-exist",
        created_by=author,
    )
    with pytest.raises(ValidationError):
        visual.full_clean()


def test_the_index_hides_a_visual_from_an_uninvolved_viewer(
    client, viewer, visual, author
):
    """Three ways to see a visual in the admin -- made it, own a dataset
    it is wired to, or are an admin. A viewer who did none of those is
    not one of them, even holding read across the application."""
    response = client.get("/visuals/")
    assert response.status_code == 200
    assert "Story geography" not in response.content.decode()


def test_its_author_sees_their_own_draft(client, visual, author):
    """The author needs a grant to reach the page at all -- somebody with
    none has no standing here -- and then sees their own draft because
    they made it, not because the grant reaches its datasets."""
    Grant.objects.create(user=author, app=DATADESK, scope="", role="viewer")
    client.force_login(author)
    assert "Story geography" in client.get("/visuals/").content.decode()


def test_the_owner_of_a_wired_dataset_sees_it(client, visual, db):
    """Union, not intersection: requiring access to every dataset a
    visual draws on would hide a cross-dataset chart from every owner who
    contributed to it."""
    visual.datasets = ["mizzou", "lehigh"]
    visual.save(update_fields=["datasets"])

    owner = User.objects.create_user("owner", email="owner@localnewsimpact.org")
    Grant.objects.create(user=owner, app=DATADESK, scope="lehigh", role="editor")
    client.force_login(owner)
    assert "Story geography" in client.get("/visuals/").content.decode()


def test_a_viewer_on_a_wired_dataset_sees_it_but_cannot_act(client, visual, db):
    """Reading the dataset is enough to see the visual -- they are
    already looking at that dataset's work. It is not enough to change
    it: that needs owning the dataset, or having made it."""
    visual.datasets = ["mizzou"]
    visual.template = "builder"
    visual.save(update_fields=["datasets", "template"])

    reader = User.objects.create_user("reader", email="reader@localnewsimpact.org")
    Grant.objects.create(user=reader, app=DATADESK, scope="mizzou", role="viewer")
    client.force_login(reader)
    assert "Story geography" in client.get("/visuals/").content.decode()
    assert client.get(f"/visuals/builder/{visual.slug}/").status_code == 403


def test_an_admin_sees_everything(client, visual, db):
    root = User.objects.create_user("boss", email="boss@localnewsimpact.org")
    Grant.objects.create(user=root, app=DATADESK, scope="", role="admin")
    client.force_login(root)
    assert "Story geography" in client.get("/visuals/").content.decode()


def test_index_requires_sign_in(client, visual):
    assert client.get("/visuals/").status_code == 302


def test_publishing_does_not_put_it_in_another_team_s_console(client, db, visual):
    """Published is public at the embed and in the bucket -- a different
    surface, reached without signing in. The admin stays scoped to what
    somebody works with, so a viewer on another dataset still does not
    see it there."""
    visual.status = Visual.PUBLISHED
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["status", "datasets"])

    elsewhere = User.objects.create_user("el", email="el@localnewsimpact.org")
    Grant.objects.create(user=elsewhere, app=DATADESK, scope="lehigh", role="viewer")
    client.force_login(elsewhere)
    assert "Story geography" not in client.get("/visuals/").content.decode()


def test_seeing_a_visual_is_not_permission_to_change_it(client, db, visual):
    """Holding `design` says somebody builds visuals; it does not say
    they build *this* one."""
    visual.template = "builder"
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["template", "datasets"])

    designer = User.objects.create_user("des", email="des@localnewsimpact.org")
    Grant.objects.create(user=designer, app=DATADESK, scope="mizzou", role="designer")
    client.force_login(designer)

    assert "Story geography" in client.get("/visuals/").content.decode()
    assert client.get(f"/visuals/builder/{visual.slug}/").status_code == 403


def test_a_revoked_author_keeps_seeing_and_stops_editing(client, db, author):
    """ROADMAP item 1: revocation changes who may edit, never what is
    public. Their own visual stays visible to them because they made it
    -- the authorship route survives losing the dataset -- and only an
    admin can act on it."""
    visual = Visual.objects.create(
        slug="mine",
        title="Mine",
        source_kind="corpus",
        template="builder",
        datasets=["mizzou"],
        created_by=author,
    )
    Grant.objects.create(user=author, app=DATADESK, scope="lehigh", role="viewer")
    client.force_login(author)

    assert "Mine" in client.get("/visuals/").content.decode()
    assert client.get(f"/visuals/builder/{visual.slug}/").status_code == 403


# --- where the publisher is, as against where the story is about -------------


def _boone_article(county="Boone", city="Columbia"):
    """One article from a publisher in a named county."""
    import uuid

    from explorer.models import Article, CandidateLink, Source

    source = Source.objects.create(
        id=str(uuid.uuid4()),
        host=f"{city}.example".lower(),
        host_norm=f"{city}.example".lower(),
        canonical_name=f"The {city} Paper",
        county=county,
        city=city,
    )
    link = CandidateLink.objects.create(
        id=str(uuid.uuid4()), url=f"https://{source.host}/1", source=source
    )
    article = Article.objects.create(
        id=str(uuid.uuid4()), status="ok", candidate_link=link
    )
    return article, source


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_spec_can_name_the_publishers_county(crawler_schema):
    """An article from a Boone County publisher is an article whose
    publisher is in Boone County -- the relation was always there as a
    dimension to group by. This is the filter key that lets a spec say it,
    which is what a map of one county's reporting needs."""
    from accounts.access import ALL_SCOPES
    from visuals.corpus import _base_queryset

    _boone_article(county="Boone", city="Columbia")
    _boone_article(county="Lafayette", city="Higginsville")

    everything = _base_queryset({}, ALL_SCOPES)
    assert everything.count() == 2

    boone = _base_queryset({"publisher_county": "Boone"}, ALL_SCOPES)
    assert boone.count() == 1


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_county_filter_ignores_case(crawler_schema):
    """Source.county is free text a human typed or an import supplied."""
    from accounts.access import ALL_SCOPES
    from visuals.corpus import _base_queryset

    _boone_article(county="Boone", city="Columbia")
    assert _base_queryset({"publisher_county": "boone"}, ALL_SCOPES).count() == 1
    assert _base_queryset({"publisher_county": "BOONE"}, ALL_SCOPES).count() == 1


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_spec_can_name_the_publishers_city(crawler_schema):
    from accounts.access import ALL_SCOPES
    from visuals.corpus import _base_queryset

    _boone_article(county="Boone", city="Columbia")
    _boone_article(county="Boone", city="Ashland")
    assert _base_queryset({"publisher_county": "Boone"}, ALL_SCOPES).count() == 2
    assert _base_queryset({"publisher_city": "Ashland"}, ALL_SCOPES).count() == 1


def test_the_builder_offers_publisher_geography():
    """A filter the spec understands and the form cannot set is a filter
    only somebody editing JSON can use."""
    from pathlib import Path

    form = (
        Path(__file__).resolve().parent.parent / "templates/visuals/builder_edit.html"
    ).read_text()
    assert 'name="f_publisher_county"' in form
    assert 'name="f_publisher_city"' in form


def test_no_filter_matches_an_outlet_by_its_host():
    """Identity is a source UUID. Matching outlets by address is what the
    proposal queue exists to keep a human deciding, so the pivot filters
    must not offer it as a quiet shortcut."""
    from pathlib import Path

    corpus = (Path(__file__).resolve().parent.parent / "visuals/corpus.py").read_text()
    base = corpus[
        corpus.index("def _base_queryset") : corpus.index("class CorpusSpecError")
    ]
    assert "host_norm" not in base
    assert "host__" not in base


# --- a dimension key is not a field name -------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_every_dimension_can_actually_be_grouped_by(crawler_schema):
    """`wire` raised "The annotation 'wire' conflicts with a field on the
    model" and the dimension was simply unusable -- Article.wire is a real
    column, and the dimension key was used verbatim as the alias.

    A dimension key is a name we chose; a model field is a name the crawler
    chose. Nothing stops them colliding, and Django raises rather than
    guessing. This runs every dimension so the next collision is a failing
    test rather than a chart nobody can build.
    """
    from accounts.access import ALL_SCOPES
    from visuals.corpus import DIMENSIONS, run_spec

    broken = []
    for key in DIMENSIONS:
        try:
            run_spec(
                {"shape": "grouped", "dimensions": [key], "measure": "articles"},
                ALL_SCOPES,
            )
        except Exception as exc:  # noqa: BLE001 - the point is which ones fail
            broken.append(f"{key}: {type(exc).__name__} {exc}")
    assert broken == [], "dimensions that cannot be grouped by: " + "; ".join(broken)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_two_dimensions_that_both_shadow_fields(crawler_schema):
    """The pair case, which is what a chord and a stacked bar both use."""
    from accounts.access import ALL_SCOPES
    from visuals.corpus import run_spec

    run_spec(
        {"shape": "grouped", "dimensions": ["wire", "status"], "measure": "articles"},
        ALL_SCOPES,
    )


# --- the embed a publisher actually pastes (ROADMAP item 22) -----------------


def _js(name):
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / f"static/js/{name}").read_text()


def test_the_snippet_carries_no_height():
    """480px was wrong for every visual and the person embedding cannot
    know the right number, because it depends on the reader's screen."""
    from visuals.admin import VisualAdmin
    from visuals.models import Visual

    visual = Visual(slug="v", title="A chart")
    visual.pk = 1
    code = str(VisualAdmin.embed_code(None, visual))
    assert "height=" not in code
    assert "datadesk-visual" in code
    assert "datadesk-embed.js" in code


def test_the_snippet_names_the_public_host_not_the_console():
    """An embed URL is written into somebody else's article and cannot be
    moved afterwards, so it must not name an implementation detail."""
    from visuals.admin import VisualAdmin
    from visuals.embed import EMBED_HOST
    from visuals.models import Visual

    visual = Visual(slug="v", title="A chart")
    visual.pk = 1
    code = str(VisualAdmin.embed_code(None, visual))
    assert EMBED_HOST == "data.localnewsimpact.org"
    assert "datadesk.localnewsimpact.org" not in code


def test_the_placeholder_holds_a_link_for_when_the_script_fails():
    """The loader appends rather than replaces, so whatever the publisher
    pasted survives. An embed that silently renders nothing is worse than
    one that renders a link."""
    from visuals.admin import VisualAdmin
    from visuals.models import Visual

    visual = Visual(slug="v", title="A chart")
    visual.pk = 1
    code = str(VisualAdmin.embed_code(None, visual))
    assert "&lt;a href=" in code
    assert "A chart" in code
    assert "node.appendChild(frame)" in _js("datadesk-embed.js")


def test_the_loader_checks_the_origin_of_every_message():
    """It runs on somebody else's page. A height message from anywhere but
    the visual's own origin is another site resizing our frame."""
    loader = _js("datadesk-embed.js")
    assert "event.origin !== ORIGIN" in loader
    assert 'data.type !== "datadesk:height"' in loader


def test_the_loader_leaves_no_trace_on_the_host_page():
    """No cookies, no analytics, one global as a guard against being
    included twice."""
    loader = _js("datadesk-embed.js")
    for forbidden in ("document.cookie", "localStorage", "fetch(", "XMLHttpRequest"):
        assert forbidden not in loader, forbidden
    assert loader.count("window.__datadeskEmbed") == 2


def test_the_frame_reports_its_height_on_change_not_on_a_timer():
    """A chart redraws when a legend wraps or a font arrives. A timer
    reports that up to a second late; an observer reports it as it
    happens."""
    reporter = _js("datadesk-embed-height.js")
    assert "ResizeObserver" in reporter
    assert "setInterval" not in reporter


def test_a_pixel_of_jitter_is_not_a_resize():
    """Reporting one would loop with a parent whose own layout shifts by a
    pixel in response."""
    assert "Math.abs(now - last) < 2" in _js("datadesk-embed-height.js")


def test_the_framed_page_loads_the_reporter():
    from pathlib import Path

    page = (
        Path(__file__).resolve().parent.parent / "templates/visuals/embed.html"
    ).read_text()
    assert "datadesk-embed-height.js" in page


# --- taking the table's data elsewhere (ROADMAP item 20) ---------------------


def _chart_js():
    from pathlib import Path

    return (
        Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"
    ).read_text()


def test_the_table_view_offers_both_formats():
    """Flourish uploads a file; Datawrapper pastes into a box, and its box
    reads tab-separated the way a spreadsheet copies. One format would
    serve one tool."""
    js = _chart_js()
    assert "Download CSV" in js
    assert "Copy for Datawrapper" in js
    assert 'asDelimited(rows, ",")' in js
    assert 'asDelimited(rows, "\\t")' in js


def test_a_value_containing_the_separator_is_quoted():
    """A comma or a quote or a newline inside a value breaks the row it
    sits in. Publisher names carry commas -- "Stone County Republican /
    Crane Chronicle" is one of ours."""
    js = _chart_js()
    assert 'replace(/"/g, \'""\')' in js
    assert '/["\\n\\r]|,|\\t/' in js


def test_the_export_carries_every_row_not_the_rendered_ones():
    """The table renders five hundred because that is what a page can
    show. Somebody exporting wants the data.

    The cap is in `oneTable`, which draws one list; the export is wired in
    `renderTable`, which walks them. Asserting on one function's body
    would pass while the other quietly changed."""
    js = _chart_js()
    drawing = js[js.index("function oneTable(") : js.index("function renderTable(")]
    walking = js[js.index("function renderTable(") :]
    assert "rows.slice(0, 500)" in drawing
    assert "rows.slice(0, 500)" not in walking, "the cap must not reach the export"
    assert "exportBar(el, rows," in walking


def test_a_refused_clipboard_leaves_something_to_copy_from():
    """Clipboard access needs a secure context and a permission that can be
    refused. A button that silently does nothing is worse than a box."""
    js = _chart_js()
    assert "window.isSecureContext" in js
    assert "dd-copybox" in js


def test_the_object_url_outlives_the_click():
    """Revoking synchronously races the click in some browsers and the file
    arrives empty."""
    js = _chart_js()
    assert "setTimeout(() => URL.revokeObjectURL(url)" in js


def test_the_view_data_button_works_when_the_feed_does_not():
    """It was wired inside the fetch's `.then`, so a feed that failed left
    the button attached to nothing: no error, no explanation, a control
    that looked live and was not."""
    from pathlib import Path

    renderer = (
        Path(__file__).resolve().parent.parent
        / "templates/visuals/renderers/builder.html"
    ).read_text()
    listener = renderer.index("toggle.addEventListener")
    fetch = renderer.index("fetch(")
    assert listener < fetch, "the toggle is wired after the fetch again"


def test_a_failed_feed_says_what_went_wrong():
    """A reader who cannot see the reason cannot tell a missing snapshot
    from a broken query."""
    from pathlib import Path

    renderer = (
        Path(__file__).resolve().parent.parent
        / "templates/visuals/renderers/builder.html"
    ).read_text()
    assert "err.message" in renderer
    assert "if (!r.ok) throw" in renderer, "a 404 resolves; only .json() would fail"


def test_the_builder_and_the_admin_hand_out_the_same_snippet():
    """It was written twice and the two drifted the first time one
    changed: the admin moved to the data host and a responsive script, and
    the builder went on offering an iframe at height 480 aimed at the
    console. An embed URL cannot be moved once pasted, so a stale snippet
    is what somebody's article loads for good."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    # The snippet lives on the publish step now; the settings page links
    # to it rather than carrying a second copy.
    template = (root / "templates/visuals/steps/publish.html").read_text()
    assert "{{ snippet }}" in template
    settings_page = (root / "templates/visuals/builder_edit.html").read_text()
    assert "datadesk-visual" not in settings_page
    # Nothing may build one by hand any more.
    for name in (
        "templates/visuals/builder_edit.html",
        "templates/visuals/steps/publish.html",
        "visuals/admin.py",
    ):
        text = (root / name).read_text()
        assert "iframe src" not in text, f"{name} still writes its own"
        assert not re.search(r'height="\d+"', text), f"{name} carries a height"


def test_the_snippet_is_built_in_one_place():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    writers = [
        p.name
        for p in [root / "visuals/admin.py", root / "visuals/views.py"]
        if "datadesk-visual" in p.read_text()
    ]
    assert writers == [], f"{writers} build the snippet instead of importing it"


# --- versions, and the caching that follows from them ------------------------
#
# `?v=` was accepted and ignored for as long as it existed: the embed script
# already appended it from data-version, and the server read the pin no
# matter what was asked for. Meanwhile the unversioned embed was served
# `immutable, max-age=31536000` -- a year -- so republishing a visual never
# reached anybody who had already loaded it.


@pytest.mark.urls("datadesk.urls_data")
def test_a_version_serves_that_version_and_nothing_else(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)  # pins v1
    _snapshot(visual, author, ROWS_V2)

    base = f"/visuals/{visual.uuid}/data.json"
    assert client.get(f"{base}?v=1").json()["data"] == ROWS_V1
    assert client.get(f"{base}?v=2").json()["data"] == ROWS_V2
    # No version asked: the pin, which is what an embed follows.
    assert client.get(base).json()["version"] == 1


@pytest.mark.urls("datadesk.urls_data")
def test_asking_for_a_version_that_is_not_there_is_not_answered_with_another(
    client, visual, author
):
    """Silently serving the pin instead is the exact failure `?v=` exists
    to prevent: the reader asked for one thing and was shown another with
    no way to tell."""
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    assert client.get(f"/visuals/{visual.uuid}/data.json?v=7").status_code == 404


@pytest.mark.urls("datadesk.urls_data")
def test_a_republished_visual_reaches_a_reader_who_already_had_it(
    client, visual, author
):
    """The unversioned URL means "current", so it cannot be immutable. A
    version names one snapshot that will never change, so it can."""
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)

    current = client.get(f"/visuals/{visual.uuid}/data.json")
    assert "immutable" not in current["Cache-Control"]

    pinned = client.get(f"/visuals/{visual.uuid}/data.json?v=1")
    assert "immutable" in pinned["Cache-Control"]

    # And the embed, which is the one that was being held for a year.
    assert "immutable" not in client.get(f"/embed/{visual.uuid}/")["Cache-Control"]
    assert "immutable" in client.get(f"/embed/{visual.uuid}/?v=1")["Cache-Control"]


def test_a_draft_preview_is_never_cached(client, viewer, visual, author):
    """A draft changes under the person previewing it, who is usually the
    person editing it. Cached at all, they would be shown their own stale
    work and conclude the save had not taken.

    On the console, because the data host has no draft to preview -- there
    is no sign-in there, so a draft is simply absent."""
    _snapshot(visual, author, ROWS_V1)
    feed = client.get("/visuals/story-geography/data.json")
    assert feed.status_code == 200
    assert feed["Cache-Control"] == "no-store"


@pytest.mark.urls("datadesk.urls_data")
def test_a_pinned_embed_fetches_the_version_it_was_pinned_to(client, visual, author):
    """The frame and the fetch inside it have to agree. An embed at ?v=1
    that renders and then fetches whatever is current is worse than one
    that ignores the version outright -- it looks pinned."""
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    _snapshot(visual, author, ROWS_V2)

    body = client.get(f"/embed/{visual.uuid}/?v=1").content.decode()
    assert f"/visuals/{visual.uuid}/data.json?v=1" in body


@pytest.mark.urls("datadesk.urls_data")
def test_a_mangled_version_is_read_as_current_rather_than_refused(
    client, visual, author
):
    """URLs pasted into articles get truncated and appended to. Answering
    with the current version is the useful reading of a request nobody
    meant to malform."""
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    for junk in ("", "abc", "-1", "0", "1.5"):
        got = client.get(f"/visuals/{visual.uuid}/data.json?v={junk}")
        assert got.status_code == 200, junk
        assert got.json()["version"] == 1


@pytest.mark.urls("datadesk.urls_data")
def test_the_slug_redirect_carries_the_version_across(client, visual, author):
    """Dropping the query string would turn a reader's ?v=1 into "whatever
    is current" without saying so."""
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    hop = client.get("/embed/story-geography/?v=1")
    assert hop.status_code == 301
    assert hop["Location"] == f"/embed/{visual.uuid}/?v=1"


def test_the_uuid_column_is_added_without_a_default():
    """`AddField` evaluates a callable default once and writes that one
    value to every existing row, so adding a unique uuid with
    `default=uuid.uuid4` gives every visual the same one and the unique
    index refuses to build:

        IntegrityError: could not create unique index
        DETAIL: Key (uuid)=(3c5ecdac-...) is duplicated.

    An empty test database never shows this -- there are no existing rows
    to collide -- which is why it is asserted on the migration itself.
    """
    from importlib import import_module

    ops = import_module("visuals.migrations.0006_visual_uuid").Migration.operations
    add = next(o for o in ops if o.__class__.__name__ == "AddField")
    assert (
        add.field.has_default() is False
    ), "a default here is applied once, to every row, identically"
    assert add.field.null is True

    # And the constraint arrives only after the values do.
    kinds = [o.__class__.__name__ for o in ops]
    assert kinds == ["AddField", "RunPython", "AlterField"]
    alter = ops[-1]
    assert alter.field.unique is True


def test_no_template_comment_spans_lines():
    """Django's `{# #}` is single-line only. Spanning lines it is not a
    comment at all -- the parser does not recognise it, and the whole
    thing renders as text on the page.

    This has now shipped twice: once in the theme gallery, where a
    five-line note drew itself as a card, and once on the public visual
    page, where a note about snapshot dates appeared above the chart.
    Both were written as prose about the code and both were published to
    readers. `{% comment %}` is what spans lines.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "templates"
    offenders = []
    for path in sorted(root.rglob("*.html")):
        text = path.read_text()
        i = 0
        while (start := text.find("{#", i)) != -1:
            end = text.find("#}", start)
            if end == -1:
                offenders.append(f"{path.name}: unclosed {{#")
                break
            if "\n" in text[start:end]:
                line = text[:start].count("\n") + 1
                offenders.append(f"{path.relative_to(root)}:{line}")
            i = end + 2
    assert (
        not offenders
    ), "these render as visible text; use {% comment %}: " + ", ".join(offenders)


# --- colour is the publisher's call, and the data comes in two formats ------

MAP_DATA = {
    "meta": {"level": "county"},
    "areas": [{"county": "Boone", "stories": 41}, {"county": "Callaway", "stories": 8}],
    "points": [{"geoid": "29019", "name": "Columbia"}],
}


@pytest.mark.urls("datadesk.urls_data")
def test_an_embed_can_be_pinned_to_a_colour(client, visual, author):
    """Left to follow the reader, a chart lands dark in the middle of a
    light article whenever that reader's laptop is set to dark. The person
    pasting it knows what their page looks like; the reader's laptop does
    not."""
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)

    loose = client.get(f"/embed/{visual.uuid}/").content.decode()
    assert "data-theme" not in loose.split("<head>")[0]

    for want in ("light", "dark"):
        body = client.get(f"/embed/{visual.uuid}/?theme={want}").content.decode()
        assert f'<html lang="en" data-theme="{want}">' in body

    # Nonsense follows the reader rather than failing, as ?v= does.
    odd = client.get(f"/embed/{visual.uuid}/?theme=chartreuse").content.decode()
    assert "data-theme" not in odd.split("<head>")[0]


def test_the_snippet_carries_the_colour_choice(visual):
    from visuals.embed import snippet

    assert "data-theme" not in snippet(visual)
    light = snippet(visual, theme="light")
    assert 'data-theme="light"' in light
    assert "theme=light" in light  # the fallback link too, not only the div


def test_the_loader_passes_the_colour_to_the_frame():
    """The attribute is useless if the script drops it on the way."""
    js = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "static/js/datadesk-embed.js"
    ).read_text()
    assert 'node.getAttribute("data-theme")' in js
    assert '"theme=" + theme' in js


@pytest.mark.urls("datadesk.urls_data")
def test_the_data_comes_as_csv_as_well_as_json(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)

    csv = client.get(f"/visuals/{visual.uuid}/data.csv")
    assert csv.status_code == 200
    assert csv["Content-Type"].startswith("text/csv")
    assert "attachment" in csv["Content-Disposition"]
    body = csv.content.decode()
    assert body.splitlines()[0] == "county,stories"
    assert "Boone,41" in body


@pytest.mark.urls("datadesk.urls_data")
def test_each_row_list_downloads_on_its_own(client, visual, author):
    """A map carries county totals and a point layer. Somebody who wants
    the totals in a spreadsheet should not have to take the points with
    them, which is the same split the table view makes on the page."""
    _snapshot(visual, author, MAP_DATA)
    publish(visual, author)

    areas = client.get(f"/visuals/{visual.uuid}/data.csv?table=areas")
    assert areas.content.decode().splitlines()[0] == "county,stories"
    points = client.get(f"/visuals/{visual.uuid}/data.csv?table=points")
    assert points.content.decode().splitlines()[0] == "geoid,name"

    # A table that is not there is a 404, not the other one.
    assert client.get(f"/visuals/{visual.uuid}/data.csv?table=meta").status_code == 404
    assert client.get(f"/visuals/{visual.uuid}/data.csv?table=nope").status_code == 404


@pytest.mark.urls("datadesk.urls_data")
def test_the_page_lists_every_file_a_reader_can_take(client, visual, author):
    _snapshot(visual, author, MAP_DATA)
    publish(visual, author)
    body = client.get(f"/visuals/{visual.uuid}/").content.decode()
    assert "data.json" in body
    assert "data.csv?table=areas" in body
    assert "data.csv?table=points" in body


def test_the_python_and_the_javascript_agree_about_what_a_table_is():
    """The table view and the download have to make the same split, or a
    reader sees two tables on the page and gets one of them in the file."""
    from visuals.views import tables_in

    assert [n for n, _ in tables_in(MAP_DATA)] == ["areas", "points"]
    assert [n for n, _ in tables_in([{"a": 1}])] == [""]
    for empty in ([], None, {}, {"meta": {"x": 1}}, "text", 7):
        assert tables_in(empty) == [], empty


# --- a visual built light stays light ----------------------------------------


def _light(visual):
    visual.config = dict(visual.config or {}, theme="datadesk", theme_mode="light")
    visual.save(update_fields=["config"])
    return visual


@pytest.mark.urls("datadesk.urls_data")
def test_a_visual_built_light_is_light_without_being_asked(client, visual, author):
    """A palette carries both a light and a dark variant and its name picks
    neither, so "I built it light" was recorded nowhere and every embed
    asked the reader's device instead. One authored light rendered dark."""
    _light(visual)
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)

    head = client.get(f"/embed/{visual.uuid}/").content.decode().split("<body")[0]
    assert 'data-theme="light"' in head


@pytest.mark.urls("datadesk.urls_data")
def test_a_pinned_embed_paints_its_own_ground(client, visual, author):
    """Transparent is right when the embed follows the reader -- it takes
    the colour of the page it lands in. Pinned, it is wrong: the host's
    dark background shows straight through a light chart, and a light
    chart on a dark page is not a light chart."""
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)

    loose = client.get(f"/embed/{visual.uuid}/").content.decode()
    assert "background: transparent" in loose

    pinned = client.get(f"/embed/{visual.uuid}/?theme=light").content.decode()
    assert "background: var(--bg)" in pinned
    assert "background: transparent" not in pinned


@pytest.mark.urls("datadesk.urls_data")
def test_the_url_still_beats_what_the_visual_says(client, visual, author):
    """Whoever pastes the embed knows what their page looks like; the
    person who built the chart three months ago does not."""
    _light(visual)
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    head = (
        client.get(f"/embed/{visual.uuid}/?theme=dark")
        .content.decode()
        .split("<body")[0]
    )
    assert 'data-theme="dark"' in head


def test_the_snippet_inherits_the_visuals_choice(visual):
    """Choosing light in the builder and having to choose it again on the
    way out is how the setting gets forgotten."""
    from visuals.embed import snippet

    _light(visual)
    assert 'data-theme="light"' in snippet(visual)
    # An explicit empty string is "follow the reader", which is a real
    # answer and must override the visual rather than inherit from it.
    assert "data-theme" not in snippet(visual, theme="")


# --- what the embed costs to load --------------------------------------------


def test_the_boundary_files_are_cached_like_everything_else_static():
    """WhiteNoise caches what has a hash in its name, which is right and
    misses the largest file the embed fetches. The chart runtime builds
    those URLs itself -- `{% static 'geo/' %}` plus a filename it picks --
    so a directory is what goes through the manifest and the files never
    do. counties-10m.json is 822KB and arrived with the 60-second default,
    re-downloaded every minute for every reader.
    """
    from datadesk.settings import _immutable_static

    assert _immutable_static("", "/static/geo/counties-10m.json")
    assert _immutable_static("", "/static/geo/tracts/29.json")
    # The manifest's own naming still passes, and nothing else does.
    assert _immutable_static("", "/static/js/d3.min.69faba9d2fff.js")
    assert not _immutable_static("", "/static/js/plain.js")


@pytest.mark.urls("datadesk.urls_data")
def test_a_map_starts_its_boundaries_before_it_needs_them(client, visual, author):
    """Otherwise they are four round trips deep: page, scripts, data.json,
    and only then does the runtime discover it wants the outlines."""
    visual.config = dict(visual.config or {}, geo_level="counties")
    visual.save(update_fields=["config"])
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)

    head = client.get(f"/embed/{visual.uuid}/").content.decode().split("</head>")[0]
    assert 'rel="preload"' in head
    assert "counties-10m.json" in head


@pytest.mark.urls("datadesk.urls_data")
def test_a_chart_with_no_map_preloads_nothing(client, visual, author):
    """A preload the page never uses is a wasted download and a console
    warning, and most visuals are not maps."""
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    head = client.get(f"/embed/{visual.uuid}/").content.decode().split("</head>")[0]
    assert 'rel="preload"' not in head


# --- what an embed downloads -------------------------------------------------


def test_a_kind_loads_only_the_libraries_it_draws_with():
    """Plot reads globalThis.d3 in its UMD factory rather than bundling it,
    so d3 comes with Plot and not instead of it. The kinds that draw their
    own SVG use d3 alone and were paying for Plot anyway; a table draws in
    plain DOM and was paying for all three -- about 164KB gzipped, to
    render a table."""
    from visuals.builder import libs_for

    assert libs_for("table") == ()
    assert libs_for("donut") == ("d3",)
    assert libs_for("storymap") == ("d3", "topojson")
    assert libs_for("bar") == ("d3", "plot")
    assert libs_for("choropleth") == ("d3", "plot", "topojson")


def test_an_unknown_kind_loads_everything():
    """A kind added without a line in the table should render slowly, not
    fail to render."""
    from visuals.builder import ALL_LIBS, libs_for

    assert libs_for("something-new") == ALL_LIBS
    assert libs_for(None) == ALL_LIBS


def test_every_kind_the_builder_offers_declares_its_libraries():
    """Otherwise it silently falls back to all three and the saving is
    lost without anybody noticing."""
    from visuals.builder import CHART_KINDS, CHART_LIBS

    kinds = {k["id"] if isinstance(k, dict) else k for k in CHART_KINDS}
    missing = kinds - set(CHART_LIBS)
    assert not missing, f"no CHART_LIBS entry for {sorted(missing)}"


def test_the_preload_and_the_fetch_name_the_same_url():
    """The boundaries are resolved through the manifest, so they carry a
    hash. Built from a bare directory instead, the runtime asked for the
    unhashed name -- a different URL from the one the page preloads, so
    the preload was wasted and the file came down twice."""
    from pathlib import Path

    js = (
        Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"
    ).read_text()
    template = (
        Path(__file__).resolve().parent.parent
        / "templates/visuals/renderers/builder.html"
    ).read_text()

    # The page resolves them...
    assert "geoUrls" in template
    assert "{% static 'geo/counties-10m.json' %}" in template
    # ...and the runtime prefers what it was given over building its own.
    assert "(urls && urls[level]) || base + spec.file" in js


@pytest.mark.urls("datadesk.urls_data")
def test_a_table_embed_ships_no_chart_libraries(client, visual, author):
    visual.config = dict(visual.config or {}, kind="table")
    visual.save(update_fields=["config"])
    visual.template = "builder"
    visual.save(update_fields=["template"])
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)

    body = client.get(f"/embed/{visual.uuid}/").content.decode()
    assert "d3.min" not in body
    assert "plot.min" not in body
    assert "topojson" not in body
    assert "datadesk-chart" in body  # it still needs the runtime itself
