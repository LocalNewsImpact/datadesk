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
    assert embed["Content-Security-Policy"] == "frame-ancestors 'self'"
    assert "X-Frame-Options" not in embed
    assert client.get("/visuals/story-geography/data.json").status_code == 200


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
    from visuals.admin import EMBED_HOST, VisualAdmin
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
    show. Somebody exporting wants the data."""
    js = _chart_js()
    table = js[js.index("function renderTable(") :]
    assert "rows.slice(0, 500)" in table
    assert "exportBar(el, rows," in table, "the cap must not reach the export"


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
