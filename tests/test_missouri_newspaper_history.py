"""The Missouri newspaper history river: a one-off renderer that still
publishes, pins and caches like every other visual."""

from unittest import mock

import pytest

from visuals.models import Visual
from visuals.services import publish, refresh_snapshot

pytestmark = pytest.mark.django_db

ROWS = [
    {
        "kind": "year",
        "year": 1870,
        "publishing": 252,
        "founded": ["Linn County news (Brookfield)"],
        "closed": [],
        "merged": [],
        "absorbed": 0,
    },
    {
        "kind": "year",
        "year": 2024,
        "publishing": 220,
        "founded": [],
        "closed": ["Nodaway News Leader (Maryville)"],
        "merged": ["Monett times \u2192 Lawrence County Record"],
        "absorbed": 1,
    },
]


@pytest.fixture
def visual(django_user_model):
    author = django_user_model.objects.create_user(
        "author", email="author@localnewsimpact.org"
    )
    v = Visual.objects.create(
        slug="missouri-newspaper-history",
        title="Missouri newspapers since 1808",
        source_kind="gcs",
        bucket_path="gs://bucket/missouri_newspaper_history.json",
        template="missouri_newspaper_history",
        config={"title": "Two centuries of Missouri newspapers"},
        created_by=author,
    )
    with mock.patch("visuals.services.fetch_source_data", return_value=ROWS):
        refresh_snapshot(v, author)
    publish(v, author)
    return v


def test_the_renderer_is_a_valid_template(visual):
    visual.full_clean()


def test_the_embed_draws_the_river(client, visual):
    page = client.get("/embed/missouri-newspaper-history/")
    assert page.status_code == 200
    body = page.content.decode()
    assert "d3.min.js" in body and "d3-sankey" not in body
    assert 'id="nr-chart"' in body
    assert "Two centuries of Missouri newspapers" in body
    assert "/visuals/missouri-newspaper-history/data.json" in body


def test_the_feed_serves_the_pinned_years(client, visual):
    feed = client.get("/visuals/missouri-newspaper-history/data.json").json()
    assert feed["version"] == 1
    assert feed["data"] == ROWS


def test_a_reader_can_see_the_papers_behind_a_year_or_a_decade(client, visual):
    """Hovering or tapping a stream -- and only a stream, not the empty row
    of its year -- lights that year's path and names its papers. Year rows
    answer only on a phone, where no streams are drawn."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert 'class="nr-card"' in body
    assert "Hover or tap a stream" in body
    assert "data(narrow ? yrs : [])" in body
    assert "dd-tip" not in body


def test_a_phone_draws_the_river_alone_and_a_tap_names_the_papers(client, visual):
    """Under 640px the river is drawn to the box's width without its streams
    or decade notes -- no sideways scroll -- and a tap on any year's row marks
    it and fills a taller details panel with that year's papers."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert "narrow = W < 640" in body
    assert "Math.max(640" not in body and "overflowX" not in body
    assert "if (TF && !narrow)" in body and "if (TT && !narrow)" in body
    assert "narrow ? [] : notes" in body
    assert "Tap the river at any year" in body
    assert ".nr-card.nr-pin.nr-tall { height: max(10.5em, 40vh); }" in body
    assert 'card.classList.toggle("nr-tall", narrow)' in body


def test_mergers_and_closures_leave_on_the_right(client, visual):
    """Gold is a paper merged into one that carried on, red one that closed;
    both leave the river at their year and gather at their decade's end."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert '"Merged"' in body and '"Closed"' in body and '"Founded"' in body
    assert "publishing today" in body


def test_no_publisher_is_printed_ahead_of_the_note(client, visual):
    note = {"note": "Missouri School of Journalism analysis"}
    Visual.objects.filter(pk=visual.pk).update(config=note)
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert "Source: Missouri School of Journalism analysis" in body
    assert "Local News Impact Consortium</a>" not in body


def test_leaving_a_stream_does_not_reset_the_river_at_once(client, visual):
    """Moving between neighbouring creeks must not flash the whole river back
    to full strength, and the details must not move the page."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert "setTimeout(reset, 350)" in body
    assert "position: fixed" in body and "pointer-events: none" in body


def test_the_details_pin_on_the_page_and_float_in_a_frame(client, visual):
    """On its own page the details pin to the top and the river slides under
    them (the figure box no longer clips the sticky panel). Framed inside a
    page that scrolls, nothing can pin to that page's window, so the details
    open beside the pointer instead."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert ".nr-card.nr-pin { position: sticky" in body
    assert ".nr-card.nr-float { position: fixed" in body
    assert "window.self !== window.top" in body
    assert 'a.style.overflow = "visible"' in body
    assert "card.style.left" in body and "card.style.top" in body


def test_the_drawn_width_is_smoothed_but_the_counts_are_exact(client, visual):
    """One-year swings in the count do not throw bumps into the banks: the
    drawn width averages the year and the years either side, and the banks
    use a curve that never overshoots. Printed counts stay exact."""
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert "d3.mean(ys, P)" in body
    assert "d3.curveMonotoneY" in body and "curveCatmullRom" not in body


TABLES = {
    "years": [
        {
            "year": 1888,
            "publishing": 519,
            "founded": 1,
            "merged": 1,
            "closed": 1,
            "founded_papers": "Vidette (St. Joseph)",
            "mergers": "Investigator + Unionville Democrat -> Democrat-Investigator",
            "closed_papers": "Hume Star (Hume)",
        },
    ],
    "papers": [
        {
            "paper": "Linn County Leader",
            "town": "Marceline",
            "county": "Linn",
            "first_year": 1886,
            "last_year": "",
            "outcome": "still publishing",
            "merged_into": "",
            "earlier_titles": "Leader; Daily News-Bulletin",
            "towns": "Marceline; Brookfield",
            "title_count": 3,
            "sources": "SHSMO catalogue",
        },
    ],
    "events": [
        {
            "year": 1888,
            "event": "founded",
            "paper": "Vidette",
            "town": "St. Joseph",
            "county": "Buchanan",
            "other_paper": "",
            "source": "SHSMO catalogue",
        },
    ],
}


def test_years_papers_and_events_download_as_their_own_csvs(
    client, visual, django_user_model
):
    """The feed is named tables; each is its own CSV, one fact per cell."""
    author = django_user_model.objects.get(username="author")
    with mock.patch("visuals.services.fetch_source_data", return_value=TABLES):
        refresh_snapshot(visual, author)
    publish(visual, author)
    for name, first_col in (("years", "year"), ("papers", "paper"), ("events", "year")):
        r = client.get(f"/visuals/missouri-newspaper-history/data.csv?table={name}")
        assert r.status_code == 200, name
        assert r.content.decode().splitlines()[0].startswith(first_col)
    papers = client.get("/visuals/missouri-newspaper-history/data.csv?table=papers")
    assert "Linn County Leader" in papers.content.decode()
    body = client.get("/embed/missouri-newspaper-history/").content.decode()
    assert "body.data.years" in body and "founded_papers" in body


def test_feeds_are_compressed_when_the_browser_accepts_it(client, visual):
    r = client.get(
        "/visuals/missouri-newspaper-history/data.json", HTTP_ACCEPT_ENCODING="gzip"
    )
    assert r.status_code == 200
    assert r.get("Content-Encoding") == "gzip"
