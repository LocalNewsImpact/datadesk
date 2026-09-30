"""The rest of item 21 of the 2026-09-30 review.

A layer switch re-ran the whole story map -- reframing, re-projecting and
regenerating every path -- to arrive at the same shapes in different
colours. The renderer now returns a handle whose `refill` recomputes the
shading and rewrites two attributes over the paths already drawn, and
refuses anything that would move a shape or a dot.
"""

from pathlib import Path

from tests.chart_runtime import run

CHART = Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"


def _story_map():
    source = CHART.read_text()
    start = source.index("  function renderStoryMap(")
    return source[start : source.index("\n  }\n", start)]


def _layer_map():
    source = CHART.read_text()
    start = source.index("  function renderLayerMap(")
    return source[start : source.index("\n  }\n", start)]


def _shading_of():
    """`renderStoryMap`'s `shadingOf`, composed from the runtime's own parts.

    The composition is three lines -- key the areas by county, take the
    painted values, band them -- and is repeated here; the banding itself
    is the runtime's, under the real d3.
    """
    return "\n".join(
        [
            "const t = T.theme();",
            "const valueOf = (a) => a.value !== undefined",
            "  ? (a.value == null ? null : Number(a.value))",
            "  : Number(a.stories ?? a.newsrooms ?? 0);",
            "const shadingOf = (areas, layerScale) => {",
            "  const { max, values } =",
            "    T.paintedValues(areas, painted, valueOf, layerScale);",
            "  return {",
            "    byCounty: new Map(areas.map((a) => [String(a.geoid), valueOf(a)])),",
            "    areaOf: new Map(areas.map((a) => [String(a.geoid), a])),",
            "    max, values, ...T.storyMapBands(values, config, layerScale, t),",
            "  };",
            "};",
        ]
    )


# --- the shading is a function of the areas and the scale ---------------------


class TestTheShadingIsRecomputable:
    def test_two_layers_over_one_frame_give_their_own_cuts(self):
        script = "\n".join(
            [
                _shading_of(),
                "const painted = new Set(['29019', '29095', '29510']);",
                "const config = {};",
                "const coverage = [",
                "  { geoid: '29019', stories: 40 },",
                "  { geoid: '29095', stories: 10 },",
                "  { geoid: '29510', stories: 4 }];",
                "const layer = [",
                "  { geoid: '29019', value: 33.1 },",
                "  { geoid: '29095', value: 37.4 },",
                "  { geoid: '29510', value: 35.9 }];",
                "const scale = { cuts: [34, 36], label: 'Median age', format: 'n' };",
                "const one = shadingOf(coverage, null);",
                "const two = shadingOf(layer, scale);",
                "console.log(JSON.stringify({",
                "  oneCuts: one.cuts, twoCuts: two.cuts,",
                "  oneHighest: one.highest, twoHighest: two.highest,",
                "  oneTop: one.shadeFor(40), twoTop: two.shadeFor(37.4),",
                "  oneMissing: one.shadeFor(0), twoZero: two.shadeFor(0),",
                "  oneLabels: one.bandLabels, oneLast: one.ramp[one.ramp.length - 1],",
                "  seqHigh: t.seqHigh, missing: t.missing,",
                "  value: two.byCounty.get('29019'),",
                "  area: two.areaOf.get('29019').value,",
                "}));",
            ]
        )
        out = run(script)
        # The layer's cuts are the server's; the coverage map computes its own.
        assert out["twoCuts"] == [34, 36]
        assert out["oneCuts"] != out["twoCuts"]
        assert out["oneHighest"] == 40 and out["twoHighest"] == 37.4
        # The darkest band is the last step of the ramp in both.
        assert out["oneTop"] == out["oneLast"] == out["seqHigh"]
        # On a story map 0 is the absence of a value; on a layer it is a value.
        assert out["oneMissing"] == out["missing"]
        assert out["twoZero"] != out["missing"]
        assert out["oneLabels"][0] == "0"
        assert out["value"] == 33.1 and out["area"] == 33.1

    def test_it_bands_only_what_is_painted(self):
        script = "\n".join(
            [
                _shading_of(),
                # Two counties drawn, five in the payload: the three outside
                # the frame must not set the cuts.
                "const painted = new Set(['29019', '29095']);",
                "const config = {};",
                "const areas = [",
                "  { geoid: '29019', stories: 40 }, { geoid: '29095', stories: 30 },",
                "  { geoid: '20091', stories: 1 }, { geoid: '17001', stories: 1 },",
                "  { geoid: '05001', stories: 1 }];",
                "const s = shadingOf(areas, null);",
                "console.log(JSON.stringify({ max: s.max, highest: s.highest,",
                "  values: s.values }));",
            ]
        )
        out = run(script)
        assert out["values"] == [30, 40]
        assert out["max"] == 40 and out["highest"] == 40


# --- the renderer applies it separately --------------------------------------


class TestTheRendererCanRepaintAlone:
    def test_the_fill_and_the_edge_are_their_own_step(self):
        body = _story_map()
        assert "const paint = () => counties" in body
        assert "shadeFor(byCounty.get(String(f.id)))" in body
        assert "\n      paint();\n" in body
        # The paths are made once, with their geometry.
        made = body.index('.data(shown).join("path")')
        assert body.index("const paint = () =>") > made
        assert (
            body[made : body.index("const paint = () =>")].count('.attr("d", path)')
            == 1
        )

    def test_the_tooltip_and_the_legend_are_rebindable(self):
        body = _story_map()
        assert "const bindCounties = () => interactive(counties, tip," in body
        assert "\n      bindCounties();\n" in body
        assert "const showLegend = () => {" in body
        assert "if (key) key.remove();" in body
        assert "\n      showLegend();\n" in body

    def test_the_handle_refuses_what_it_cannot_refill(self):
        body = _story_map()
        handle = body[body.index("      return {\n        refill(next) {") :]
        # A different level, or a different set of points, moves shapes.
        assert '(meta.level === "tract" ? "tracts" : "counties") !== geoLevel' in handle
        assert "(next.points || []) !== points) return false" in handle
        # What it does when it accepts: the shading, the fills, the tooltip,
        # the legend -- and nothing about the projection or the paths.
        for call in (
            "shadingOf(next.areas || [], layerScale)",
            "paint();",
            "bindCounties();",
            "showLegend();",
        ):
            assert call in handle, call
        for absent in ("geoAlbersUsa", "geoPath", 'attr("d"', "svgRoot"):
            assert absent not in handle, absent

    def test_the_renderer_hands_the_handle_back(self):
        body = _story_map()
        assert (
            "return boundaries(opts.geoBase, geoLevel, ids, opts.geoUrls).then(" in body
        )


class TestTheLayeredMapAsksTheHandleFirst:
    def test_a_switch_refills_before_it_redraws(self):
        body = _layer_map()
        assert "if (drawn && drawn.refill(composed)) return;" in body
        assert "drawing.then((handle) => { drawn = handle || null; });" in body

    def test_the_points_off_state_is_one_array(self):
        body = _layer_map()
        assert "const NO_POINTS = [];" in body
        assert "points: showPoints ? points : NO_POINTS," in body
        assert "points : []," not in body

    def test_the_empty_sentence_drops_the_handle(self):
        """It replaced the svg, so there is nothing left to refill."""
        body = _layer_map()
        empty = body[body.index('map.textContent = "Nothing shown') :]
        assert "drawn = null;" in empty[: empty.index("return;")]
