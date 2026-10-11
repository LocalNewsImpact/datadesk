"""The Missouri newspaper event map: a one-off renderer that still publishes,
pins and caches like every other visual."""

from unittest import mock

import pytest

from visuals.models import Visual
from visuals.services import publish, refresh_snapshot

pytestmark = pytest.mark.django_db

FEED = {
    "y0": 1808,
    "years": [{"year": 1808, "publishing": 1}, {"year": 1809, "publishing": 1}],
    "events": [
        {
            "year": 1808,
            "event": "founded",
            "paper": "Missouri Gazette",
            "town": "St. Louis",
            "county": "St. Louis",
            "x": -90.2448,
            "y": 38.6609,
            "approx": False,
        },
        {
            "year": 1809,
            "event": "moved",
            "paper": "Missouri Gazette",
            "town": "St. Louis",
            "county": "St. Louis",
            "x": -90.2448,
            "y": 38.6609,
            "approx": False,
            "to": "Louisiana Gazette",
            "into_town": "Franklin",
            "into_county": "Howard",
            "ix": -92.7522,
            "iy": 39.0114,
        },
    ],
    "today": [
        {
            "paper": "Vandalia Leader",
            "town": "Vandalia",
            "county": "Audrain",
            "since": 1875,
            "x": -91.4929,
            "y": 39.3055,
        }
    ],
    "counties": {"29510": [1, 1]},
    "now": {"29007": 1},
}


@pytest.fixture
def visual(django_user_model):
    author = django_user_model.objects.create_user(
        "author", email="author@localnewsimpact.org"
    )
    v = Visual.objects.create(
        slug="missouri-newspaper-event-map",
        title="Missouri newspapers, year by year",
        source_kind="gcs",
        bucket_path="gs://bucket/missouri_newspaper_event_map.json",
        template="missouri_newspaper_event_map",
        config={
            "title": "Missouri newspapers, year by year",
            "subtitle": "Each year's foundings, mergers, moves and closings.",
        },
        created_by=author,
    )
    with mock.patch("visuals.services.fetch_source_data", return_value=FEED):
        refresh_snapshot(v, author)
    publish(v, author)
    return v


def test_the_renderer_is_a_valid_template(visual):
    visual.full_clean()


def test_the_embed_draws_the_map_with_the_sites_own_scripts(client, visual):
    body = client.get("/embed/missouri-newspaper-event-map/").content.decode()
    assert 'id="em-map"' in body and 'id="em-spark"' in body
    assert "d3.min.js" in body and "topojson-client.min.js" in body
    assert "cdn.jsdelivr.net" not in body
    assert "geo/counties-10m.json" in body
    assert "/visuals/missouri-newspaper-event-map/data.json" in body
    assert "Missouri newspapers, year by year" in body


def test_beside_the_panel_the_map_is_centred_in_the_full_height_box(client, visual):
    """Sized to its own shape on a wide screen, the map sat at the top of its
    box, against the legend, with the spare height left above the credits.
    Only a phone gets the map's own height; elsewhere the box is the frame's
    height and the map sits centred in it, as the approved prototype drew it."""
    body = client.get("/embed/missouri-newspaper-event-map/").content.decode()
    assert "(narrow ? mw * H / W : wrapEl.clientHeight)" in body
    assert 'mapEl.style.height = mw * H / W + "px"' not in body


def test_radio_and_tv_stations_are_triangles_counted_with_the_newsrooms(client, visual):
    """A station whose news we collect is drawn as a triangle in the event
    colours from its first air date: a founding that year, in its county's
    shading and in what is active today -- never in the river or the trees,
    whose papers the feed keeps apart."""
    body = client.get("/embed/missouri-newspaper-event-map/").content.decode()
    assert "const ST = D.stations || [];" in body
    assert 'e.medium === "broadcast" ? TRI : CIRC' in body
    assert "shade(papers(f.id) + air(f.id))" in body
    assert 'c.set("founded", (c.get("founded") || 0) + ST.filter' in body
    assert "active today" in body and "on the air" in body
    assert "Radio or TV station" in body and "Still active today" in body
    assert "first on the air ${e.year}" in body


def test_the_map_keeps_only_the_room_the_panel_overhangs(client, visual):
    """The counts and the timeline panel sit over the map's own east side, so
    the map no longer gives up a fixed 290px beside it: it is as wide as the
    frame allows, less what the panel overhangs, and limited by height it keeps
    10px clear of the legend."""
    body = client.get("/embed/missouri-newspaper-event-map/").content.decode()
    assert "wrapW - 290" not in body
    assert "(wrapW - 300) / fx" in body
    assert "2 * Math.max(0, 10 - lead)" in body


def test_the_feed_serves_the_pinned_map(client, visual):
    feed = client.get("/visuals/missouri-newspaper-event-map/data.json").json()
    assert feed["version"] == 1
    assert feed["data"] == FEED


@pytest.mark.urls("datadesk.urls_data")
def test_the_page_gives_the_map_the_standard_frame(client, visual):
    """In the 80rem frame the map, held by the window's height to about 830px,
    left 170-280px empty to its right at desktop widths; in the standard frame
    the panel ends at the frame's edge (measured at 900-1920px wide)."""
    body = client.get(f"/visuals/{visual.uuid}/").content.decode()
    assert 'class="wrap"' in body and 'class="wrap wide"' not in body


@pytest.mark.urls("datadesk.urls_data")
def test_the_page_shows_its_downloads_and_embed_offer(client, visual):
    """The data panel started hidden for every visual and only the chart
    builder's data toggle revealed it, so a one-off renderer's downloads
    and embed snippets were on the page and could not be seen."""
    body = client.get(f"/visuals/{visual.uuid}/").content.decode()
    assert '<div id="dd-takeaway" class="takeaway">' in body
    assert "Embed this visual" in body and "table=events" in body


def test_paper_names_are_escaped_in_the_details(client, visual):
    body = client.get("/embed/missouri-newspaper-event-map/").content.decode()
    assert "esc(e.paper)" in body and "esc(where(e.town, e.county))" in body


def test_events_and_papers_today_download_as_their_own_csvs(client, visual):
    for name, first_col in (("events", "year"), ("today", "paper"), ("years", "year")):
        r = client.get(f"/visuals/missouri-newspaper-event-map/data.csv?table={name}")
        assert r.status_code == 200, name
        assert r.content.decode().splitlines()[0].startswith(first_col)
