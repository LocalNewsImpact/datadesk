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


class TestTheDataStep:
    def test_a_story_map_asks_what_to_map(self, client, author, crawler_schema):
        visual = _visual(author, "storymap")
        body = client.get(f"/visuals/builder/{visual.slug}/step/data/").content.decode()
        assert 'name="layer" value="newsrooms"' in body

    def test_another_chart_is_not_asked(self, client, author, crawler_schema):
        visual = _visual(author, "bar")
        body = client.get(f"/visuals/builder/{visual.slug}/step/data/").content.decode()
        assert 'name="layer"' not in body

    def test_newsrooms_is_saved(self, client, author, crawler_schema):
        visual = _visual(author, "storymap")
        client.post(
            f"/visuals/builder/{visual.slug}/step/data/",
            {"layer": "newsrooms", "subset": "complete"},
        )
        visual.refresh_from_db()
        assert visual.spec["layer"] == "newsrooms"


class TestTheFeed:
    def test_a_newsroom_story_map_draws_the_registry(
        self, tmp_path, author, crawler_schema
    ):
        from visuals.outlets import import_registry
        from visuals.services import fetch_source_data

        import_registry(_registry(tmp_path, ROWS))
        visual = _visual(author, "storymap")
        visual.spec = {"layer": "newsrooms"}
        visual.save()
        payload = fetch_source_data(visual)
        assert payload["meta"]["unit"] == "newsrooms"
        assert len(payload["points"]) == 2


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
        assert "? { stroke: t.surface, width: 1, inked: false }" in js
        assert ": { stroke: t.ink, width: 1.5, inked: true };" in js
        assert 'return category === "collected"' in js
        stroke = '.attr("stroke", (p) => (byCategory ? ringOf(p).stroke : t.surface))'
        assert stroke in js
        assert "if (ring.inked) dot.style.boxShadow" in js
        assert '(a.category === "collected") - (b.category === "collected")' in js
