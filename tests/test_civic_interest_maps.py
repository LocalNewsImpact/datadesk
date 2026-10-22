"""Civic interest areas, coverage density, and the swipe between them
(docs/CIVIC_INTEREST_MAPS.md).

The two maps arrive as finished figures -- every shape already projected
into the map's frame -- and the swipe lays one over the other. What these
hold: the kinds are wired through the builder, a figure is never offered
as a download, a swipe refuses two maps that do not share a frame, and the
renderer's scales read the way the maps are described to readers.
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from tests.chart_runtime import value
from visuals.builder import CHART_KINDS, libs_for
from visuals.models import Visual
from visuals.types import BY_ID

FIGURE_KINDS = ("civicarea", "coveragemap")


def _figure(measure, town="Columbia", rank=2, W=354, H=338):
    return {
        "figure": 1,
        "measure": measure,
        "region": "columbia",
        "town": town,
        "rank": rank,
        "W": W,
        "H": H,
        "hexes": [
            {"id": "1,1", "d": "M1,1L2,1L2,2Z", "c": [1.5, 1.5], "v": 12.0, "n": 9}
        ],
        "counties": [{"id": "29019", "d": "M0,0L10,0L10,10Z", "home": True}],
        "ring": [],
        "labels": [],
        "leaders": [],
        "dots": [],
        "market": "",
        "scale": (
            {"kind": "log", "cap": 9, "breadth_full": 8}
            if measure == "coverage"
            else {"kind": "bands", "floor": 5, "cuts": [10, 20, 40, 80]}
        ),
    }


# --- wiring ----------------------------------------------------------------


def test_the_three_kinds_reach_the_builder():
    for kind in (*FIGURE_KINDS, "swipemap"):
        assert kind in BY_ID
        assert kind in CHART_KINDS
        # Drawn from a projected figure: d3 only, no boundary files.
        assert libs_for(kind) == ("d3",)


def test_a_figure_map_walks_type_look_and_publish():
    """Nothing to slice and nothing to pick: the figure is finished."""
    from visuals.steps import steps_for

    for kind in (*FIGURE_KINDS, "swipemap"):
        visual = SimpleNamespace(config={"kind": kind}, source_kind="gcs", spec={})
        assert [s.slug for s in steps_for(visual)] == ["type", "theme", "publish"]


def test_a_figure_offers_no_table_to_download():
    """Its lists are paths and labels, and its values come from licensed
    travel data: neither the table view nor the CSV offers them."""
    from visuals.views import tables_in

    assert tables_in(_figure("area")) == []
    assert tables_in({"swipe": 1, "left": {}, "right": {}}) == []
    # Every other payload still lists its tables.
    assert tables_in({"points": [{"a": 1}], "meta": {}}) == [("points", [{"a": 1}])]


def test_a_swipe_names_its_two_maps_or_asks_for_them():
    from visuals.sentence import parts_for

    empty = SimpleNamespace(config={"kind": "swipemap"}, spec={}, bucket_path="")
    kinds = [k for _, _, k in parts_for(empty)]
    assert kinds.count("gap") == 2
    full = SimpleNamespace(
        config={"kind": "swipemap", "swipe_left": "cov", "swipe_right": "area"},
        spec={},
        bucket_path="",
    )
    assert [k for _, _, k in parts_for(full)].count("gap") == 0


# --- the swipe's data ----------------------------------------------------------


@pytest.fixture
def author(db):
    return User.objects.create_user("maker", email="maker@localnewsimpact.org")


def _figure_visual(author, slug, kind, figure):
    from visuals.services import record_snapshot

    visual = Visual.objects.create(
        slug=slug,
        title=slug,
        source_kind="gcs",
        bucket_path=f"gs://bucket/{slug}.json",
        config={"kind": kind},
        created_by=author,
    )
    record_snapshot(visual, author, figure)
    return visual


@pytest.mark.django_db
def test_a_swipe_carries_both_figures(author):
    from visuals.services import compose_swipe

    _figure_visual(author, "cov", "coveragemap", _figure("coverage"))
    _figure_visual(author, "area", "civicarea", _figure("area"))
    data = compose_swipe({"swipe_left": "cov", "swipe_right": "area"})
    assert data["swipe"] == 1
    assert (
        data["left"]["kind"] == "coveragemap" and data["right"]["kind"] == "civicarea"
    )
    assert data["left"]["version"] == 1
    assert data["left"]["figure"]["measure"] == "coverage"


@pytest.mark.django_db
def test_a_swipe_refuses_two_areas_that_do_not_line_up(author):
    """A divider between Columbia II and Mexico would line up nothing."""
    from visuals.services import DataSourceError, compose_swipe

    _figure_visual(author, "cov", "coveragemap", _figure("coverage"))
    _figure_visual(
        author, "mexico", "civicarea", _figure("area", town="Mexico", rank=1)
    )
    with pytest.raises(DataSourceError, match="same area"):
        compose_swipe({"swipe_left": "cov", "swipe_right": "mexico"})


@pytest.mark.django_db
def test_a_swipe_only_takes_civic_maps(author):
    from visuals.services import DataSourceError, compose_swipe, record_snapshot

    other = Visual.objects.create(
        slug="bars",
        title="bars",
        source_kind="inline",
        config={"kind": "bar"},
        created_by=author,
    )
    record_snapshot(other, author, [{"a": 1}])
    _figure_visual(author, "area", "civicarea", _figure("area"))
    with pytest.raises(DataSourceError, match="not a civic interest area"):
        compose_swipe({"swipe_left": "bars", "swipe_right": "area"})
    with pytest.raises(DataSourceError, match="Choose a map"):
        compose_swipe({"swipe_left": "", "swipe_right": "area"})


@pytest.mark.django_db
def test_a_swipe_is_fetched_from_its_maps_whatever_its_source(author):
    from visuals.services import fetch_source_data

    _figure_visual(author, "cov", "coveragemap", _figure("coverage"))
    _figure_visual(author, "area", "civicarea", _figure("area"))
    swipe = Visual.objects.create(
        slug="swipe",
        title="swipe",
        source_kind="corpus",
        config={"kind": "swipemap", "swipe_left": "cov", "swipe_right": "area"},
        created_by=author,
    )
    assert fetch_source_data(swipe)["swipe"] == 1


# --- the renderer's scales -------------------------------------------------------


def test_the_area_bands_follow_the_floor_and_cuts():
    """Floor 5, cuts 10/20/40/80: below 5 is not shaded, 80+ is the darkest."""
    scale = "{floor: 5, cuts: [10, 20, 40, 80]}"
    got = value(f"[4, 5, 10, 39, 80, 100].map((v) => T.civicBand(v, {scale}))")
    assert got == [0, 1, 2, 3, 5, 5]


def test_coverage_tops_out_at_frequent():
    """Nine stories a month and nine hundred read the same: the map shows
    whether a place is covered often, not how big it is."""
    got = value("[0, 2, 9, 900].map((v) => +T.coverageTone(v, 9).toFixed(3))")
    assert got[0] == 0 and 0 < got[1] < 1
    assert got[2] == 1 and got[3] == 1


def test_breadth_is_full_size_at_eight_kinds():
    got = value("[0, 4, 8, 10].map((n) => +T.breadthScale(n, 8).toFixed(2))")
    assert got == [0.4, 0.7, 1.0, 1.0]


def test_a_shrunken_hexagon_keeps_its_centre():
    got = value("T.pathPoints(T.scaledHex('M0,0L4,0L4,4L0,4Z', [2, 2], 0.5))")
    assert got == [[1, 1], [3, 1], [3, 3], [1, 3]]


# --- the walk: made, typed, styled, published, embedded -------------------------


@pytest.fixture
def designer(client):
    from accounts.models import DATADESK, Grant

    user = User.objects.create_user("designer", email="d@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return user


class _Blob:
    FIGURES = {"cov.json": _figure("coverage"), "area.json": _figure("area")}

    def __init__(self, name):
        self.name = name

    def download_as_bytes(self):
        import json

        return json.dumps(self.FIGURES[self.name.rsplit("/", 1)[-1]]).encode()


class _Client:
    def bucket(self, _name):
        return SimpleNamespace(blob=_Blob)


@pytest.mark.django_db(databases=["default", "crawler"])
class TestTheWalk:
    def test_two_figures_and_a_swipe_to_working_embeds(
        self, client, designer, monkeypatch
    ):
        """The walk every chart type must survive, for all three: each map made
        from its figure, typed, styled and published, then the swipe made of
        the two and read by somebody with no session."""
        from unittest import mock

        from django.test import Client

        monkeypatch.setattr("google.cloud.storage.Client", _Client, raising=False)

        def walk(title, source, kind, look, **made):
            with mock.patch("google.cloud.storage.Client", _Client):
                client.post(
                    "/visuals/builder/new/",
                    {"title": title, "source_kind": source, **made},
                )
                visual = Visual.objects.get(title=title)
                for name, fields in (
                    ("type", {"kind": kind}),
                    ("theme", look),
                    ("publish", {"do": "publish"}),
                ):
                    got = client.post(
                        f"/visuals/builder/{visual.slug}/step/{name}/",
                        dict(fields, stay="1"),
                    )
                    assert got.status_code in (200, 302), f"{name}: {got.status_code}"
                visual.refresh_from_db()
            assert visual.status == Visual.PUBLISHED, kind
            page = Client().get(f"/embed/{visual.slug}/")
            assert page.status_code == 200
            assert f'"kind": "{kind}"' in page.content.decode()
            return visual

        cov = walk(
            "Columbia II coverage",
            "gcs",
            "coveragemap",
            {"theme": "lnic"},
            bucket_path="gs://maps/civic/cov.json",
        )
        area = walk(
            "Columbia II area",
            "gcs",
            "civicarea",
            {"theme": "lnic"},
            bucket_path="gs://maps/civic/area.json",
        )
        assert cov.pinned_snapshot.data["measure"] == "coverage"
        swipe = walk(
            "Columbia II swipe",
            "corpus",
            "swipemap",
            {"theme": "lnic", "opt-swipe_left": cov.slug, "opt-swipe_right": area.slug},
        )
        data = swipe.pinned_snapshot.data
        assert data["left"]["slug"] == cov.slug and data["right"]["slug"] == area.slug
