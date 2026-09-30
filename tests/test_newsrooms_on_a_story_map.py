"""Newsrooms on a story map.

The outlet registry -- every Missouri newsroom any of our lists knows about --
drawn as a story map: a dot per outlet coloured by what it is, and each county
shaded by how many outlets are located there. The registry is built in the
crawler repo and imported here into `outlet_registry`.
"""

import csv

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from visuals.models import Outlet, Visual

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

FIELDS = [
    "outlet_id",
    "source_id",
    "outlet",
    "state",
    "city",
    "county",
    "county_fips",
    "address",
    "lat",
    "lon",
    "location_basis",
    "host",
    "owner",
    "status",
    "status_basis",
    "merged_into",
    "aka",
    "map",
    "map_category",
    "march_articles",
]


def _registry(tmp_path, rows):
    path = tmp_path / "registry.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    return path


ROWS = [
    {
        "outlet_id": "a1",
        "outlet": "Columbia Missourian",
        "city": "Columbia",
        "county": "Boone",
        "county_fips": "29019",
        "lat": "38.95",
        "lon": "-92.33",
        "map": "yes",
        "map_category": "collected",
        "march_articles": "225",
    },
    {
        "outlet_id": "a2",
        "outlet": "Boone County Journal",
        "city": "Ashland",
        "county": "Boone",
        "county_fips": "29019",
        "lat": "38.77",
        "lon": "-92.26",
        "map": "yes",
        "map_category": "replica",
    },
    {
        "outlet_id": "a3",
        "outlet": "No Point Weekly",
        "city": "",
        "county": "Boone",
        "county_fips": "29019",
        "map": "yes",
        "map_category": "print",
    },
    {
        "outlet_id": "a4",
        "outlet": "Old Paper",
        "city": "Fulton",
        "county": "Callaway",
        "county_fips": "29027",
        "lat": "38.84",
        "lon": "-91.95",
        "map": "no",
        "status": "closed",
    },
    {
        "outlet_id": "a5",
        "outlet": "Legal Ledger",
        "city": "Fulton",
        "county": "Callaway",
        "county_fips": "29027",
        "lat": "38.84",
        "lon": "-91.95",
        "map": "no",
        "map_category": "legal",
        "status": "legal",
    },
]


class TestTheImport:
    def test_it_loads_every_row(self, tmp_path):
        from visuals.outlets import import_registry

        counts = import_registry(_registry(tmp_path, ROWS))
        assert counts == {
            "rows": 5,
            "created": 5,
            "updated": 0,
            "removed": 0,
            "events_applied": 0,
        }
        m = Outlet.objects.get(outlet_id="a1")
        assert (m.name, m.county_fips, m.on_map, m.category, m.march_articles) == (
            "Columbia Missourian",
            "29019",
            True,
            "collected",
            225,
        )
        assert Outlet.objects.get(outlet_id="a3").lat is None

    def test_the_file_is_the_registry(self, tmp_path):
        """An outlet the file no longer holds is removed; the rest updated."""
        from visuals.outlets import import_registry

        import_registry(_registry(tmp_path, ROWS))
        counts = import_registry(_registry(tmp_path, ROWS[:2]))
        assert counts == {
            "rows": 2,
            "created": 0,
            "updated": 2,
            "removed": 3,
            "events_applied": 0,
        }
        assert set(Outlet.objects.values_list("outlet_id", flat=True)) == {"a1", "a2"}

    def test_a_repeated_id_is_refused_and_nothing_changes(self, tmp_path):
        from visuals.outlets import import_registry

        import_registry(_registry(tmp_path, ROWS[:1]))
        with pytest.raises(ValueError):
            import_registry(_registry(tmp_path, [ROWS[1], ROWS[1]]))
        assert list(Outlet.objects.values_list("outlet_id", flat=True)) == ["a1"]

    def test_a_file_that_is_not_the_registry_is_refused(self, tmp_path):
        from visuals.outlets import import_registry

        path = tmp_path / "other.csv"
        path.write_text("name,city\nx,y\n")
        with pytest.raises(ValueError):
            import_registry(path)


class TestTheMap:
    def test_a_point_per_outlet_on_the_map_with_a_place(self, tmp_path):
        from visuals.outlets import import_registry, run_outlet_map

        import_registry(_registry(tmp_path, ROWS))
        payload = run_outlet_map()
        assert sorted(p["name"] for p in payload["points"]) == [
            "Boone County Journal",
            "Columbia Missourian",
        ]
        assert {p["category"] for p in payload["points"]} == {"collected", "replica"}

    def test_a_county_counts_every_outlet_located_there(self, tmp_path):
        """Boone has three on the map, one without a point; Callaway's two
        are closed and legal, so it is not shaded."""
        from visuals.outlets import import_registry, run_outlet_map

        import_registry(_registry(tmp_path, ROWS))
        areas = {a["geoid"]: a["newsrooms"] for a in run_outlet_map()["areas"]}
        assert areas == {"29019": 3}

    def test_it_says_what_it_counts(self, tmp_path):
        from visuals.outlets import import_registry, run_outlet_map

        import_registry(_registry(tmp_path, ROWS))
        meta = run_outlet_map()["meta"]
        assert meta["unit"] == "newsrooms"
        assert meta["categories"] == ["collected", "replica"]


@pytest.fixture
def author(client):
    user = User.objects.create_user("designer", email="d@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return user


def _visual(author, kind):
    return Visual.objects.create(
        slug=f"map-{kind}",
        title="Map",
        template="builder",
        source_kind="corpus",
        created_by=author,
        config={"kind": kind},
    )


class TestTheOutletMapType:
    """The registry is its own chart type, drawn by the story map's renderer:
    a newsroom layer inside the story map could not be styled from the
    builder, so it looked like nothing else there."""

    def test_it_is_offered(self):
        from visuals.builder import CHART_KINDS, CHART_LIBS
        from visuals.types import BY_ID

        assert "outletmap" in CHART_KINDS
        assert CHART_LIBS["outletmap"] == CHART_LIBS["storymap"]
        assert BY_ID["outletmap"].family == BY_ID["storymap"].family

    def test_its_walk_is_type_look_publish(self, author):
        from visuals.steps import steps_for

        visual = _visual(author, "outletmap")
        assert [s.slug for s in steps_for(visual)] == ["type", "theme", "publish"]

    def test_a_story_map_no_longer_asks_what_to_map(
        self, client, author, crawler_schema
    ):
        visual = _visual(author, "storymap")
        body = client.get(f"/visuals/builder/{visual.slug}/step/data/").content.decode()
        assert 'name="layer"' not in body

    def test_the_look_step_offers_its_colours(self, client, author, crawler_schema):
        visual = _visual(author, "outletmap")
        url = f"/visuals/builder/{visual.slug}/step/theme/"
        body = client.get(url).content.decode()
        assert 'name="opt-colour_collected"' in body
        assert 'name="opt-categories_drawn" value="social"' in body
        assert 'name="opt-shade_by"' in body

    def test_a_colour_is_a_palette_slot(self, client, author, crawler_schema):
        visual = _visual(author, "outletmap")
        client.post(
            f"/visuals/builder/{visual.slug}/step/theme/",
            {
                "opt-colour_collected": "3",
                "opt-colour_print": "#ff0000",
                "opt-categories_drawn": ["collected", "print"],
                "opt-outline": "none",
            },
        )
        visual.refresh_from_db()
        assert visual.config["colour_collected"] == "3"
        # Not a slot: refused rather than stored.
        assert visual.config["colour_print"] == ""
        assert visual.config["categories_drawn"] == "collected, print"
        assert visual.config["outline"] == "none"


class TestTheWalk:
    def test_to_a_working_embed(self, client, author, tmp_path, crawler_schema):
        """Made on the new-visual form, typed, styled, published, and read by
        somebody with no session -- the walk every chart type must survive."""
        from django.test import Client

        from visuals.outlets import import_registry

        import_registry(_registry(tmp_path, ROWS))
        client.post(
            "/visuals/builder/new/",
            {"title": "Missouri outlets", "source_kind": "corpus"},
        )
        visual = Visual.objects.get(slug="missouri-outlets")

        def press(name, **fields):
            got = client.post(
                f"/visuals/builder/{visual.slug}/step/{name}/", dict(fields, stay="1")
            )
            assert got.status_code in (200, 302), f"{name}: {got.status_code}"
            visual.refresh_from_db()

        press("type", kind="outletmap")
        press("theme", theme="datadesk", **{"opt-colour_collected": "2"})
        press("publish", do="publish")
        assert visual.status == Visual.PUBLISHED
        assert len(visual.pinned_snapshot.data["points"]) == 2
        page = Client().get(f"/embed/{visual.slug}/")
        assert page.status_code == 200
        assert '"kind": "outletmap"' in page.content.decode()


class TestItsSentenceAndFrame:
    """It could not finish its sentence -- "A outlet map from any date in
    every dataset" -- because it has no dates or datasets, and with no
    newsrooms step it had no frame, so every neighbouring state's counties
    were drawn."""

    def test_the_look_step_completes_it(self, client, author, crawler_schema):
        from visuals.sentence import article, is_complete, parts_for

        visual = _visual(author, "outletmap")
        assert not is_complete(visual)
        client.post(f"/visuals/builder/{visual.slug}/step/theme/", {"theme": ""})
        visual.refresh_from_db()
        parts = parts_for(visual)
        text = " ".join(p for lead, t, _ in parts for p in (lead, t) if p)
        assert text == "outlet map of every outlet in the registry in Missouri"
        assert article(parts) == "An"
        assert is_complete(visual)

    def test_it_frames_its_state_whole_by_default(self, client, author, crawler_schema):
        visual = _visual(author, "outletmap")
        client.post(f"/visuals/builder/{visual.slug}/step/theme/", {"theme": ""})
        visual.refresh_from_db()
        assert visual.config["focus"] == "29"
        assert visual.config["extent"] == "state"
        assert len(visual.config["frame"]) == 115
        assert all(c.startswith("29") for c in visual.config["frame"])

    def test_a_place_can_be_typed(self, client, author, crawler_schema):
        visual = _visual(author, "outletmap")
        client.post(
            f"/visuals/builder/{visual.slug}/step/theme/",
            {"focus": "Boone", "focus_level": "", "extent": "selected"},
        )
        visual.refresh_from_db()
        assert visual.config["focus"] == "29019"
        assert visual.config["frame"] == ["29019"]

    def test_the_kinds_drawn_are_said(self, client, author, crawler_schema):
        from visuals.sentence import parts_for

        visual = _visual(author, "outletmap")
        client.post(
            f"/visuals/builder/{visual.slug}/step/theme/",
            {"opt-categories_drawn": ["print", "replica"]},
        )
        visual.refresh_from_db()
        assert ("of", "print, replica outlets", "said") in parts_for(visual)

    def test_a_story_map_still_starts_with_a(self, author):
        from visuals.sentence import article, parts_for

        assert article(parts_for(_visual(author, "storymap"))) == "A"


class TestTheFeed:
    def test_an_outlet_map_draws_the_registry(self, tmp_path, author, crawler_schema):
        from visuals.outlets import import_registry
        from visuals.services import fetch_source_data

        import_registry(_registry(tmp_path, ROWS))
        payload = fetch_source_data(_visual(author, "outletmap"))
        assert payload["meta"]["unit"] == "newsrooms"
        assert len(payload["points"]) == 2

    def test_only_the_kinds_drawn(self, tmp_path):
        from visuals.outlets import import_registry, run_outlet_map

        import_registry(_registry(tmp_path, ROWS))
        payload = run_outlet_map({"categories_drawn": "replica"})
        assert {p["category"] for p in payload["points"]} == {"replica"}
        # Shaded by what is drawn, so the county counts the replica alone.
        assert sum(a["newsrooms"] for a in payload["areas"]) == 1

    def test_shaded_by_what_we_collect(self, tmp_path):
        from visuals.outlets import import_registry, run_outlet_map

        import_registry(_registry(tmp_path, ROWS))
        payload = run_outlet_map({"shade_by": "collected"})
        assert len(payload["points"]) == 2
        assert sum(a["newsrooms"] for a in payload["areas"]) == 1
        assert payload["meta"]["unit"] == "outlets we collect from"

    def test_no_shading(self, tmp_path):
        from visuals.outlets import import_registry, run_outlet_map

        import_registry(_registry(tmp_path, ROWS))
        assert run_outlet_map({"shade_by": "none"})["areas"] == []


class TestTheRenderer:
    def test_newsroom_dots_are_coloured_by_what_they_are(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        js = (root / "static/js/datadesk-chart.js").read_text()
        order = '["collected", "not collected", "print", "replica", "social"]'
        assert f"const NEWSROOM_CATEGORIES = {order};" in js
        assert "byCategory ? colourOf(p.category)" in js
        # Not the theme's first colours in order: that put "collected" in the
        # county ramp's own blue. Colours near the ramp's hue are passed over.
        assert "function newsroomColours(t)" in js
        assert "gap(hueOf(c), ramp) >= 30" in js
        assert "newsroom${beyond === 1" in js
        assert "`${unit} located in each county:`" in js

    def test_every_newsroom_we_did_not_collect_is_ringed_in_ink(self):
        """Hue alone did not separate collected from not at dot size. Every
        kind we did not collect wears an ink ring, in the key as on the map;
        collected keeps the surface ring and is drawn on top."""
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        js = (root / "static/js/datadesk-chart.js").read_text()
        assert "? { stroke: t.surface, width: 0.5, inked: false }" in js
        assert ": { stroke: t.ink, width: 0.5, inked: true };" in js
        assert 'return category === "collected"' in js
        # A thin dark ring on every dot, on or off.
        thin = '{ stroke: "#161616", width: 0.5, inked: true }'
        assert f'if (outline === "thin") return {thin};' in js
        # "No rings" draws no ring, not a surface-coloured one.
        assert (
            'if (outline === "none") return { stroke: "none", width: 0, inked: false };'
            in js
        )
        stroke = '.attr("stroke", (p) => (byCategory ? ringOf(p).stroke : t.surface))'
        assert stroke in js
        # The author's colour, a slot of the theme's own palette.
        assert 'config[`colour_${c.replace(/ /g, "_")}`]' in js
        assert "if (ring.inked) swatch.style.boxShadow" in js
        assert '(a.category === "collected") - (b.category === "collected")' in js
