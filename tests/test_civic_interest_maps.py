"""Civic interest areas, coverage density, and the swipe between them
(docs/CIVIC_INTEREST_MAPS.md).

The area map is drawn from an uploaded table, the coverage map counts the
corpus over that area's hexagons, and the swipe lays one over the other.
What these hold: the kinds are wired through the builder; the frame and
projection are the analysis's; a map at a state line shows the next
state's counties; coverage follows the analysis's rules; a figure is never
offered as a download; a swipe refuses two maps that do not share a frame;
and the renderer's scales read the way the maps are described to readers.
"""

import math
from types import SimpleNamespace
from unittest import mock

import pytest
from django.contrib.auth.models import User

from tests.chart_runtime import value
from visuals import civic
from visuals.builder import CHART_KINDS, libs_for
from visuals.models import Visual
from visuals.types import BY_ID

FIGURE_KINDS = ("civicarea", "coveragemap")
COLUMBIA, INDEPENDENCE = "2915670", "2935000"


def _table(town="Columbia", place=COLUMBIA, areas=(1, 2), region="columbia"):
    """A small civic interest area table: the town's own hexagons as home,
    a ring of shaded ones around them, and the next ring drawn empty."""
    g = civic.grid()
    home = [k for k in g["places"][place][4] if g["hexes"][k][1] == place]
    near = set()
    for k in home:
        near |= set(civic.hexes_within(*civic.hex_centre(k), 9))
    shaded = sorted(near - set(home))
    edge = sorted(
        {n for k in shaded for n in civic.hexes_within(*civic.hex_centre(k), 3)}
        - set(home)
        - set(shaded)
    )
    rows = []
    for area in areas:
        row = {
            "region": region,
            "town": town,
            "area": area,
            "residents": 50000,
            "share": 0.4,
        }
        rows += [dict(row, hex=k, travel=100, lift=0, shown=1, home=1) for k in home]
        rows += [
            dict(
                row,
                hex=k,
                travel=12 + i % 40,
                lift=0.3 if i % 7 == 0 else 0,
                shown=1,
                home=0,
            )
            for i, k in enumerate(shaded)
        ]
        rows += [dict(row, hex=k, travel=0, lift=0, shown=0, home=0) for k in edge]
    return rows


# --- wiring ----------------------------------------------------------------


def test_the_three_kinds_reach_the_builder():
    for kind in (*FIGURE_KINDS, "swipemap"):
        assert kind in BY_ID
        assert kind in CHART_KINDS
        # Projected on the server, county borders included: d3 only.
        assert libs_for(kind) == ("d3",)


def test_each_map_walks_the_steps_it_needs():
    """The area and the swipe choose everything on the Look step; the
    coverage map also chooses its months and datasets."""
    from visuals.steps import steps_for

    for kind in ("civicarea", "swipemap"):
        visual = SimpleNamespace(config={"kind": kind}, source_kind="inline", spec={})
        assert [s.slug for s in steps_for(visual)] == ["type", "theme", "publish"]
    visual = SimpleNamespace(
        config={"kind": "coveragemap"}, source_kind="corpus", spec={}
    )
    assert [s.slug for s in steps_for(visual)] == ["type", "theme", "data", "publish"]


def test_a_figure_offers_no_table_to_download():
    """Its lists are paths and labels, and its values come from licensed
    travel data: neither the table view nor the CSV offers them."""
    from visuals.views import tables_in

    assert tables_in({"figure": 1, "hexes": [{"id": "1,1"}]}) == []
    assert tables_in({"swipe": 1, "left": {}, "right": {}}) == []
    # Every other payload still lists its tables.
    assert tables_in({"points": [{"a": 1}], "meta": {}}) == [("points", [{"a": 1}])]


def test_each_map_names_what_it_still_needs():
    from visuals.sentence import parts_for

    def gaps(kind, config, spec=None):
        visual = SimpleNamespace(
            config={"kind": kind, **config}, spec=spec or {}, bucket_path=""
        )
        return [k for _, _, k in parts_for(visual)].count("gap")

    assert gaps("swipemap", {}) == 2
    assert gaps("swipemap", {"swipe_left": "cov", "swipe_right": "area"}) == 0
    assert gaps("civicarea", {}) == 2
    assert gaps("civicarea", {"civic_table": "t", "civic_town": "Linn"}) == 0
    assert gaps("coveragemap", {"civic_map": "a"}) == 1
    assert (
        gaps(
            "coveragemap",
            {"civic_map": "a"},
            {"from": "2026-03-01", "to": "2026-03-31"},
        )
        == 0
    )


# --- the grid and the frame -------------------------------------------------------


def test_a_hexagon_is_three_km_across():
    lon, lat = civic.hex_centre("-9000,3000")
    corners = civic.hex_corners("-9000,3000")
    g = civic.grid()
    km = [math.hypot((x - lon) * g["kx"], (y - lat) * g["ky"]) for x, y in corners]
    assert all(abs(d - 1.5) < 1e-9 for d in km)


def test_a_neighbourhood_is_its_hexagon_and_the_ring_around_it():
    """3 km from a hexagon's centre reaches the six beside it (3 km apart,
    centre to centre) and no further."""
    g = civic.grid()
    key = g["places"][COLUMBIA][4][0]
    assert len(civic.hexes_within(*civic.hex_centre(key), 3.0)) == 7


def test_the_frame_fills_the_figure():
    """The analysis's fit: the frame touches both padded edges of one
    dimension and is centred in the other."""
    frame = [[-93.5, 38.1], [-91.2, 38.1], [-93.5, 39.74], [-91.2, 39.74]]
    proj = civic.Projection(frame)
    xs, ys = zip(*(proj(*p) for p in frame), strict=True)
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    assert math.isclose(span_x, civic.W - 8) or math.isclose(span_y, civic.H - 8)
    assert math.isclose(min(xs) + max(xs), civic.W) and math.isclose(
        min(ys) + max(ys), civic.H
    )
    # North is up.
    assert proj(-92.3, 39.5)[1] < proj(-92.3, 38.5)[1]


def test_the_frame_keeps_the_home_county_in_view():
    """Travel weighted far to one side still leaves the home county inside."""
    far = civic.hexes_within(-90.3, 38.6, 3)  # St. Louis
    frame = civic.frame_of(dict.fromkeys(far, 100), "29019")  # Boone
    x0, y0, x1, y1 = civic._bounds(civic.grid()["counties"]["29019"][1])
    assert frame[0][0] <= x0 and frame[3][0] >= x1
    assert frame[0][1] <= y0 and frame[3][1] >= y1


# --- the area map ------------------------------------------------------------------


def test_an_area_is_drawn_from_its_table():
    fig = civic.area_figure(_table(), "Columbia", 2)
    assert fig["title"] == "Columbia's civic interest area II"
    assert fig["home_county"] == "29019"
    assert fig["W"] == civic.W and fig["H"] == civic.H
    assert {c["id"] for c in fig["counties"] if c["home"]} == {"29019"}
    home = [lab for lab in fig["labels"] if lab["text"] == "Columbia"]
    assert home and home[0]["weight"] == 700
    lifted = [h for h in fig["hexes"] if "lift" in h]
    assert lifted and all(h["lift"] >= 0.1 for h in lifted)
    # The footprint is every hexagon the table draws, shaded or empty.
    assert len(fig["footprint"]) == len(fig["hexes"]) + len(fig["ring"])


def test_one_area_has_no_number():
    fig = civic.area_figure(_table(areas=(1,)), "Columbia", 1)
    assert fig["title"] == "Columbia's civic interest area"


def test_a_map_at_a_state_line_shows_the_next_states_counties():
    """Independence sits on the Kansas line: Wyandotte and Johnson, Kansas,
    are drawn for context (user, 2026-10-22)."""
    rows = _table("Independence", INDEPENDENCE, areas=(1,), region="kansascity")
    fig = civic.area_figure(rows, "Independence", 1)
    states = {c["id"][:2] for c in fig["counties"]}
    assert {"29", "20"} <= states
    # Hexagons stay Missouri's.
    assert all(civic.grid()["hexes"].get(h["id"]) for h in fig["hexes"])


def test_an_area_the_table_lacks_is_named():
    with pytest.raises(civic.CivicError, match="no Mexico area 1"):
        civic.area_figure(_table(), "Mexico", 1)
    with pytest.raises(civic.CivicError, match="not a civic interest area table"):
        civic.area_figure([{"a": 1}], "Columbia", 1)


# --- coverage density ---------------------------------------------------------------


def _story(
    sid,
    scope="city_municipality",
    level="place",
    geoid=COLUMBIA,
    label="Education",
    lonlat=(None, None),
    town=None,
):
    return (sid, scope, level, geoid, lonlat[0], lonlat[1], label, town)


def _town_reach():
    return civic.grid()["places"][COLUMBIA][4]


def test_a_town_story_counts_by_the_square_root_of_its_size():
    reach = _town_reach()
    values, _ = civic.coverage_values([_story("a")], [], reach)
    expect = min(1.0, (7 / len(reach)) ** 0.5)
    assert all(math.isclose(values[k], expect) for k in reach)


def test_a_county_story_is_counted_once_where_people_live():
    """Spread by population, a county story adds up to seven hexagons'
    worth -- the same as a town of seven -- however large the county."""
    g = civic.grid()
    boone = g["county_hexes"]["29019"]
    values, _ = civic.coverage_values(
        [_story("a", level="county", geoid="29019")], [], boone
    )
    assert math.isclose(sum(values.values()), 7.0)
    busiest = max(boone, key=lambda k: g["hexes"][k][2])
    emptiest = min(boone, key=lambda k: g["hexes"][k][2])
    assert values[busiest] > values[emptiest]


def test_sports_counts_half_and_a_mention_half_again():
    reach = _town_reach()
    full, _ = civic.coverage_values([_story("a")], [], reach)
    sport, _ = civic.coverage_values([_story("a", label="Sports")], [], reach)
    k = reach[0]
    assert math.isclose(sport[k], full[k] / 2)
    # A story about elsewhere that names the town.
    named, _ = civic.coverage_values(
        [_story("b", geoid="2946586")], [("b", "place", COLUMBIA)], reach
    )
    assert math.isclose(named[k], full[k] / 2)


def test_a_statewide_story_and_its_mentions_add_nothing():
    reach = _town_reach()
    values, _ = civic.coverage_values(
        [_story("a", scope="statewide")],
        [("a", "place", COLUMBIA), ("a", "county", "29019")],
        reach,
    )
    assert not any(values.values())


def test_a_hexagon_keeps_a_storys_strongest_claim():
    """A story centred on the town that also names it counts once."""
    reach = _town_reach()
    once, _ = civic.coverage_values([_story("a")], [], reach)
    named, _ = civic.coverage_values([_story("a")], [("a", "place", COLUMBIA)], reach)
    assert once == named


def test_a_point_in_no_town_reaches_five_km():
    g = civic.grid()
    rural = next(k for k, v in g["hexes"].items() if v[0] == "29019" and not v[1])
    lon, lat = civic.hex_centre(rural)
    values, _ = civic.coverage_values(
        [_story("a", level="block", geoid="290190001001000", lonlat=(lon, lat))],
        [],
        [rural],
    )
    n = len(civic.hexes_within(lon, lat, 5.0))
    assert math.isclose(values[rural], min(1.0, (7 / n) ** 0.5))


def test_breadth_counts_the_kinds_with_a_story_a_month():
    """Ten stories of one kind are volume, not breadth."""
    g = civic.grid()
    k = g["places"][COLUMBIA][4][0]
    lon, lat = civic.hex_centre(k)
    near = lambda i, label: _story(  # noqa: E731
        str(i),
        scope="neighborhood_community",
        level="block",
        geoid="x",
        label=label,
        lonlat=(lon, lat),
    )
    narrow = [near(i, "Education") for i in range(10)]
    _, breadth = civic.coverage_values(narrow, [], [k])
    assert breadth[k] == 1
    broad = [near(i, c) for i, c in enumerate(civic.CATEGORIES[:8])]
    _, breadth = civic.coverage_values(broad, [], [k])
    assert breadth[k] == 8
    # Per month: the same eight over two months is half a story a month each.
    _, breadth = civic.coverage_values(broad, [], [k], months=2)
    assert breadth[k] == 0


def test_a_month_is_a_month():
    assert civic.months_in({"from": "2026-03-01", "to": "2026-03-31"}) == 1.0
    assert civic.months_in({"from": "2026-02-01", "to": "2026-04-30"}) == 3.0
    assert math.isclose(
        civic.months_in({"from": "2026-03-01", "to": "2026-03-15"}), 15 / 30.4375
    )
    with pytest.raises(civic.CivicError, match="Data step"):
        civic.months_in({})


def test_the_coverage_map_takes_its_areas_frame():
    area = civic.area_figure(_table(), "Columbia", 2)
    reach = _town_reach()
    values, breadth = civic.coverage_values([_story("a")], [], area["footprint"])
    cov = civic.coverage_figure(area, values, breadth, "March 2026")
    assert cov["frame"] == area["frame"]
    assert cov["title"] == "Columbia's civic interest area II: coverage density"
    assert {h["id"] for h in cov["hexes"]} == set(reach) & set(area["footprint"])
    assert len(cov["hexes"]) + len(cov["ring"]) == len(area["footprint"])
    assert sum(
        cov["shares"][t] for t in ("frequent", "regular", "sporadic", "none")
    ) in (99, 100, 101)


# --- the swipe's data ----------------------------------------------------------


@pytest.fixture
def author(db):
    return User.objects.create_user("maker", email="maker@localnewsimpact.org")


def _figure_visual(author, slug, kind, figure):
    from visuals.services import record_snapshot

    visual = Visual.objects.create(
        slug=slug,
        title=slug,
        source_kind="inline",
        config={"kind": kind},
        created_by=author,
    )
    record_snapshot(visual, author, figure)
    return visual


def _figures():
    area = civic.area_figure(_table(), "Columbia", 2)
    values, breadth = civic.coverage_values([_story("a")], [], area["footprint"])
    return area, civic.coverage_figure(area, values, breadth)


@pytest.mark.django_db
def test_a_swipe_carries_both_figures(author):
    from visuals.services import compose_swipe

    area, cov = _figures()
    _figure_visual(author, "cov", "coveragemap", cov)
    _figure_visual(author, "area", "civicarea", area)
    data = compose_swipe({"swipe_left": "cov", "swipe_right": "area"})
    assert data["swipe"] == 1
    assert (
        data["left"]["kind"] == "coveragemap" and data["right"]["kind"] == "civicarea"
    )
    assert data["left"]["version"] == 1
    assert data["left"]["figure"]["measure"] == "coverage"


@pytest.mark.django_db
def test_a_swipe_refuses_two_areas_that_do_not_line_up(author):
    """A divider between Columbia II and Columbia I would line up nothing:
    same town, different frames."""
    from visuals.services import DataSourceError, compose_swipe

    area, cov = _figures()
    other = dict(area, rank=1)
    _figure_visual(author, "cov", "coveragemap", cov)
    _figure_visual(author, "one", "civicarea", other)
    with pytest.raises(DataSourceError, match="same area"):
        compose_swipe({"swipe_left": "cov", "swipe_right": "one"})


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
    _figure_visual(author, "area", "civicarea", _figures()[0])
    with pytest.raises(DataSourceError, match="not a civic interest area"):
        compose_swipe({"swipe_left": "bars", "swipe_right": "area"})
    with pytest.raises(DataSourceError, match="Choose a map"):
        compose_swipe({"swipe_left": "", "swipe_right": "area"})


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


# --- the walk: uploaded, made, typed, styled, published, embedded ---------------


@pytest.fixture
def designer(client):
    from accounts.models import DATADESK, Grant

    user = User.objects.create_user("designer", email="d@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return user


def _csv(rows):
    import csv
    import io

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


@pytest.mark.django_db(databases=["default", "crawler"])
class TestTheWalk:
    def test_a_table_two_maps_and_a_swipe_to_working_embeds(
        self, client, designer, crawler_schema
    ):
        """The walk every chart type must survive, for all three: the table
        uploaded; the area made from it, the coverage map from the corpus
        over that area, and the swipe of the two, each typed, styled and
        published, then read by somebody with no session."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import Client

        client.post(
            "/visuals/builder/new/",
            {
                "title": "Civic interest area table",
                "source_kind": "inline",
                "file": SimpleUploadedFile(
                    "areas.csv", _csv(_table()).encode(), content_type="text/csv"
                ),
            },
        )
        table = Visual.objects.get(title="Civic interest area table")

        def walk(title, source, kind, steps):
            client.post(
                "/visuals/builder/new/", {"title": title, "source_kind": source}
            )
            visual = Visual.objects.get(title=title)
            for name, fields in (
                ("type", {"kind": kind}),
                *steps,
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

        look = {
            "theme": "lnic",
            "opt-civic_table": table.slug,
            "opt-civic_town": "Columbia",
            "opt-civic_area": "2",
        }
        area = walk("Columbia II area", "corpus", "civicarea", [("theme", look)])
        assert area.pinned_snapshot.data["title"] == "Columbia's civic interest area II"

        stories = ([_story("a")], [])
        with mock.patch("visuals.civic.stories_for", return_value=stories) as asked:
            cov = walk(
                "Columbia II coverage",
                "corpus",
                "coveragemap",
                [
                    ("theme", {"theme": "lnic", "opt-civic_map": area.slug}),
                    ("data", {"from": "2026-03-01", "to": "2026-03-31"}),
                ],
            )
        assert asked.called
        assert cov.spec["from"] == "2026-03-01"
        data = cov.pinned_snapshot.data
        assert (
            data["measure"] == "coverage"
            and data["frame"] == area.pinned_snapshot.data["frame"]
        )

        swipe = walk(
            "Columbia II swipe",
            "corpus",
            "swipemap",
            [
                (
                    "theme",
                    {
                        "theme": "lnic",
                        "opt-swipe_left": cov.slug,
                        "opt-swipe_right": area.slug,
                    },
                )
            ],
        )
        data = swipe.pinned_snapshot.data
        assert data["left"]["slug"] == cov.slug and data["right"]["slug"] == area.slug
