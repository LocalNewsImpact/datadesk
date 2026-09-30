"""A layered map: newsrooms and coverage as the base, Census measures over it.

Up to eight Census layers from the registry, at county or tract level, each
with a scale cut on the server in its own units and its unreliable cells
marked; the two base layers the author's to keep (docs/LAYERED_MAP.md).
"""

import json

import pytest
from django.test import Client

from accounts.models import DATADESK, Grant
from tests.test_builder_steps import (
    author,
    corpus,
    dataset,
    newsroom,
    two_newsrooms,
)
from visuals import layermap
from visuals.models import CensusValue, Outlet, Visual
from visuals.panels import layers_panel
from visuals.steps import reached, steps_for

# The walk's fixtures, borrowed from the builder-steps tests: a corpus
# with a dataset and two newsrooms. Named here so they read as used.
__all__ = ["author", "corpus", "dataset", "newsroom", "two_newsrooms"]

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def _value(geoid, variable, level="county", **kw):
    row = {"estimate": 100, "moe": 10, "percent": 12.0, "percent_moe": 1.5}
    row.update(kw)
    return CensusValue.objects.create(
        year=2024, level=level, geoid=geoid, variable=variable, **row
    )


class TestTheRegistryOfLayers:
    def test_unknown_and_repeated_layers_are_dropped_and_the_cap_holds(self):
        config = {
            "layers": [{"variable": "median_age"}, {"variable": "median_age"}]
            + [{"variable": "nope"}]
            + [
                {"variable": k}
                for k in (
                    "under_18",
                    "age_65_and_over",
                    "households",
                    "housing_units",
                    "owner_occupied",
                    "median_gross_rent",
                    "median_home_value",
                    "in_labor_force",
                )
            ]
        }
        kept = layermap.layers_of(config)
        assert len(kept) == layermap.MAX_LAYERS
        assert kept[0] == {"variable": "median_age", "as": ""}

    def test_the_level_is_county_unless_tract(self):
        assert layermap.level_of({}) == "county"
        assert layermap.level_of({"layer_level": "tract"}) == "tract"
        assert layermap.level_of({"layer_level": "state"}) == "county"

    def test_cuts_are_rising_deciles_in_the_measures_own_precision(self):
        cuts = layermap.cuts_for([float(v) for v in range(1, 101)])
        assert cuts == [10.9, 20.8, 30.7, 40.6, 50.5, 60.4, 70.3, 80.2, 90.1]
        assert layermap.cuts_for([65388.0, 72758.0, 56160.0], steps=3) == [
            62312.0,
            67845.0,
        ]
        assert layermap.cuts_for([1.0]) == []
        assert layermap.cuts_for([5.0] * 20) == []  # every quantile ties

    def test_a_place_is_named(self):
        assert layermap.place_name("29019", "county") == "Boone, MO"
        assert layermap.place_name("29001950100", "tract") == "Tract 9501, Adair, MO"
        assert layermap.place_name("29019001203", "tract") == "Tract 12.03, Boone, MO"


class TestALayer:
    def test_a_share_is_a_percent_unless_asked_for_the_count(self):
        _value(
            "29019", "under_18", percent=20.1, percent_moe=1.0, estimate=37800, moe=1900
        )
        _value(
            "29173", "under_18", percent=21.2, percent_moe=1.2, estimate=2210, moe=140
        )
        pct = layermap.census_layer({"variable": "under_18"}, "county", "29", {})
        assert pct["format"] == "percent" and pct["label"] == "Under 18"
        assert [a["value"] for a in pct["areas"]] == [20.1, 21.2]
        count = layermap.census_layer(
            {"variable": "under_18", "as": "value"}, "county", "29", {}
        )
        assert count["format"] == "number"
        assert [a["value"] for a in count["areas"]] == [37800, 2210]

    def test_money_is_money_and_a_wide_margin_is_marked(self):
        _value("29019", "median_household_income", estimate=72758, moe=2100)
        _value("29173", "median_household_income", estimate=65388, moe=40000)
        layer = layermap.census_layer(
            {"variable": "median_household_income"}, "county", "29", {}
        )
        assert layer["format"] == "dollars"
        assert [a["unreliable"] for a in layer["areas"]] == [False, True]
        assert layer["unreliable"] == 1
        assert layer["areas"][1]["name"] == "Ralls, MO"

    def test_only_the_frames_state_is_read(self):
        _value("29019", "median_age", estimate=33)
        _value("17001", "median_age", estimate=41)
        assert (
            len(
                layermap.census_layer({"variable": "median_age"}, "county", "29", {})[
                    "areas"
                ]
            )
            == 1
        )
        assert layermap._state_of({"frame": ["17001"]}) == "17"
        assert layermap._state_of({"focus": "29019"}) == "29"
        assert layermap._state_of({}) == "29"


def _outlet(oid="o1", **kw):
    row = {
        "name": "KOMU",
        "status": "active",
        "category": "collected",
        "on_map": True,
        "county_fips": "29019",
        "lat": 38.95,
        "lon": -92.33,
        "row": {},
    }
    row.update(kw)
    return Outlet.objects.create(outlet_id=oid, **row)


class TestThePayload:
    def test_points_coverage_and_layers_come_together(
        self, corpus, dataset, monkeypatch
    ):
        # The coverage layer is the story map's areas, whatever it makes of
        # the slice; what matters here is that they are carried through.
        monkeypatch.setattr(
            "visuals.corpus.run_story_map",
            lambda spec, scopes, config=None: {
                "points": [],
                "areas": [{"geoid": "29019", "county name": "Boone, MO", "stories": 7}],
                "meta": {},
            },
        )
        _outlet()
        _value("29019", "median_age", estimate=33, moe=1)
        spec = {"datasets": [dataset.slug], "subset": "complete"}
        config = {"kind": "layermap", "layers": [{"variable": "median_age"}]}
        data = layermap.run_layer_map(spec, ["*"], config)
        assert data["meta"]["level"] == "county"
        assert [p["name"] for p in data["points"]] == ["KOMU"]
        assert data["meta"]["coverage"] is True
        (area,) = data["areas"]
        assert (area["geoid"], area["stories"]) == ("29019", 7)
        assert [layer["id"] for layer in data["layers"]] == ["median_age"]
        assert data["layers"][0]["areas"][0]["value"] == 33

    def test_the_base_layers_can_be_turned_off(self, corpus, dataset):
        _outlet()
        spec = {"datasets": [dataset.slug], "subset": "complete"}
        config = {"kind": "layermap", "base_points": False, "base_coverage": False}
        data = layermap.run_layer_map(spec, ["*"], config)
        assert data["points"] == [] and data["areas"] == [] and data["layers"] == []
        assert "empty_because" in data["meta"]

    def test_coverage_is_by_county_only(self, corpus, dataset):
        _value("29019001203", "median_age", "tract", estimate=33, moe=1)
        spec = {"datasets": [dataset.slug], "subset": "complete"}
        config = {
            "kind": "layermap",
            "layer_level": "tract",
            "layers": [{"variable": "median_age"}],
            "base_points": False,
        }
        data = layermap.run_layer_map(spec, ["*"], config)
        assert data["areas"] == []
        assert (
            data["meta"]["coverage_note"] == "coverage shading is drawn by county only"
        )
        assert data["layers"][0]["areas"][0]["name"] == "Tract 12.03, Boone, MO"


class TestTheWalkAndItsSteps:
    def test_a_layered_map_has_a_layers_step_and_no_fields_step(self, author):
        visual = Visual.objects.create(
            slug="lm",
            title="LM",
            source_kind="corpus",
            created_by=author,
            config={"kind": "layermap"},
        )
        slugs = [s.slug for s in steps_for(visual)]
        assert "layers" in slugs and "fields" not in slugs
        assert slugs.index("layers") > slugs.index("places")
        story = Visual.objects.create(
            slug="sm",
            title="SM",
            source_kind="corpus",
            created_by=author,
            config={"kind": "storymap"},
        )
        assert "layers" not in [s.slug for s in steps_for(story)]
        visual.config["layers"] = [{"variable": "median_age"}]
        assert "layers" in reached(visual)


class TestThePanel:
    def _visual(self, author, **config):
        return Visual.objects.create(
            slug="lm",
            title="LM",
            source_kind="corpus",
            created_by=author,
            config={"kind": "layermap", **config},
        )

    def test_it_offers_every_measure_grouped_with_its_tract_flag(self, author):
        panel = layers_panel(self._visual(author, layers=[{"variable": "median_age"}]))
        assert panel["level"] == "county" and panel["max"] == 8 and panel["picked"] == 1
        offered = [v for g in panel["groups"] for v in g["variables"]]
        assert len(offered) == 28
        assert next(v for v in offered if v["key"] == "median_age")["on"]
        assert not next(v for v in offered if v["key"] == "hispanic")["tract"]
        assert panel["base_points"] and panel["base_coverage"]

    def test_it_writes_the_layers_and_refuses_too_many_or_the_wrong_level(self, author):
        from django.http import QueryDict

        visual = self._visual(author)
        post = QueryDict(mutable=True)
        post.setlist("layer", ["median_age", "under_18"])
        post.update(
            {"layer_level": "county", "as_under_18": "value", "base_points": "1"}
        )
        written = layers_panel(visual, post)["config"]
        assert written["layers"] == [
            {"variable": "median_age", "as": ""},
            {"variable": "under_18", "as": "value"},
        ]
        assert written["base_points"] is True and written["base_coverage"] is False

        post = QueryDict(mutable=True)
        post.setlist("layer", ["hispanic"])
        post["layer_level"] = "tract"
        with pytest.raises(ValueError, match="county only: Hispanic"):
            layers_panel(visual, post)

        post = QueryDict(mutable=True)
        post.setlist("layer", [v["key"] for v in layermap.census.VARIABLES[:9]])
        with pytest.raises(ValueError, match="At most 8"):
            layers_panel(visual, post)

        post = QueryDict(mutable=True)
        post.setlist("layer", ["nope"])
        with pytest.raises(ValueError, match="No such measure"):
            layers_panel(visual, post)


class TestTheWalk:
    def test_to_a_working_embed(self, client, author, corpus, two_newsrooms, dataset):
        """Typed, styled, sliced, layered, published, and read by somebody
        with no session -- the walk every chart type must survive."""
        Grant.objects.get_or_create(user=author, app=DATADESK, scope="", role="editor")
        client.force_login(author)
        _outlet()
        _value("29019", "median_age", estimate=33, moe=1)
        client.post(
            "/visuals/builder/new/", {"title": "Layered Boone", "source_kind": "corpus"}
        )
        visual = Visual.objects.get(slug="layered-boone")

        def press(name, **fields):
            got = client.post(
                f"/visuals/builder/{visual.slug}/step/{name}/", dict(fields, stay="1")
            )
            assert got.status_code in (200, 302), f"{name}: {got.status_code}"
            visual.refresh_from_db()

        press("type", kind="layermap")
        press("theme", theme="datadesk", theme_mode="light")
        press(
            "data",
            datasets=[dataset.slug],
            subset="complete",
            **{"from": "2026-03-01", "to": "2026-03-31"},
        )
        press(
            "layers",
            layer_level="county",
            layer=["median_age"],
            base_points="1",
            base_coverage="1",
        )
        assert visual.config["layers"] == [{"variable": "median_age", "as": ""}]
        body = client.get(
            f"/visuals/builder/{visual.slug}/step/publish/"
        ).content.decode()
        assert "Nothing to publish yet" not in body
        press("publish", do="publish")
        assert visual.status == Visual.PUBLISHED
        data = visual.pinned_snapshot.data
        assert [layer["id"] for layer in data["layers"]] == ["median_age"]
        assert data["points"] and data["meta"]["layers"] == 1
        page = Client().get(f"/embed/{visual.slug}/")
        assert page.status_code == 200
        assert '"kind": "layermap"' in page.content.decode()

    def test_its_sentence_names_its_layers(self, author):
        from visuals.sentence import is_complete, parts_for

        visual = Visual.objects.create(
            slug="lm",
            title="LM",
            source_kind="corpus",
            created_by=author,
            config={"kind": "layermap", "theme": "datadesk"},
            spec={"datasets": ["mizzou"], "from": "2026-03-01", "to": "2026-03-31"},
        )
        parts = parts_for(visual)
        assert ("shading by", "some Census layers", "layers") in [
            (p[0], p[1], p[2]) for p in parts
        ] or not is_complete(visual)
        visual.config["layers"] = [{"variable": "median_age"}, {"variable": "under_18"}]
        parts = parts_for(visual)
        assert ("shading by", "2 Census layers", "said") in parts


def test_the_renderer_speaks_the_layers_units():
    """fmtValue and scaleLabels, run in node."""
    from tests.test_a_table_is_a_pivot import _node

    assert _node('T.fmtValue(12.345, "percent")') == "12.3%"
    assert _node('T.fmtValue(65388, "dollars")') == "$65,388"
    assert _node('T.fmtValue(33.4, "number")') == "33.4"
    assert _node('T.fmtValue(null, "number")') == ""
    assert _node('T.scaleLabels({format: "percent"}, [10, 20], 35)') == [
        "no estimate",
        "up to 10.0%",
        "10.0%–20.0%",
        "over 20.0%",
    ]
    assert json.dumps(
        _node('T.scaleLabels({format: "dollars"}, [], 900)')
    ) == json.dumps(["no estimate", "up to $900"])
