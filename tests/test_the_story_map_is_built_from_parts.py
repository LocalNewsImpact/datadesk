"""Items 25 and 26 of the 2026-09-30 review.

`renderStoryMap` was 567 lines with dead code (`roundish`, never called)
and a legend built swatch by swatch; the frame selection was written
twice, once here and once in the flow map. Three legend builders drew a
swatch and a label three ways, the font string appeared five times, and
two renderers hand-built an svg root beside `svgRoot`.

Split into `framedBy`, `storyMapLegend`, `rampMarks` and one
`swatchItem`/`legend` pair, with `FONT` and `svgRoot` shared. The
behaviour is unchanged: what each part draws is asserted by running it.
"""

import re
from pathlib import Path

from tests.chart_runtime import run

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "static/js/datadesk-chart.js"


def _function(source, name):
    start = source.index(f"function {name}(")
    return source[start : source.index("\n  }", start) + 4]


# --- the renderer is smaller and has no dead code -----------------------------


class TestTheRendererIsBuiltFromParts:
    def test_the_story_map_is_shorter_than_it_was(self):
        source = CHART.read_text()
        start = source.index("  function renderStoryMap(")
        body = source[start : source.index("\n  }\n", start)]
        # 567 lines before the split.
        assert len(body.split("\n")) < 400

    def test_the_parts_exist_and_the_renderer_calls_them(self):
        source = CHART.read_text()
        for name in ("framedBy", "storyMapLegend", "rampMarks", "swatchItem", "legend"):
            assert f"  function {name}(" in source, name
        start = source.index("  function renderStoryMap(")
        body = source[start : source.index("\n  }\n", start)]
        assert "framedBy(features, focus, chosen," in body
        # Through showLegend, which a layer switch calls again.
        assert "key = storyMapLegend(t, {" in body

    def test_the_dead_rounding_helper_is_gone(self):
        assert "roundish" not in CHART.read_text()

    def test_the_legend_reads_no_config(self):
        """The renderer reads the config; its parts are handed what they
        need, so `test_the_story_map_declares_what_it_reads` keeps
        covering everything."""
        source = CHART.read_text()
        for name in ("storyMapLegend", "rampMarks"):
            body = _function(source, name)
            assert "config." not in body, name


# --- the frame selection is one function for both maps ------------------------


class TestOneFrameSelection:
    def test_it_picks_a_list_a_county_a_state_or_auto(self):
        script = "\n".join(
            [
                "const features = ['29019', '29095', '20091', '17001'].map(",
                "  (id) => ({ id }));",
                "const idOf = (f) => String(f.id);",
                "const auto = (all) => all.filter((f) => f.id === '17001');",
                "const ids = (out) => out.map((f) => f.id);",
                "const frame = (focus, chosen) =>",
                "  ids(T.framedBy(features, focus, chosen, idOf, auto));",
                "console.log(JSON.stringify({",
                "  list: frame('', ['29019', '20091']),",
                "  county: frame('29019', []),",
                "  state: frame('29', []),",
                "  auto: frame('', []),",
                "  listWins: frame('29', ['17001']),",
                "}));",
            ]
        )
        out = run(script, libs=())
        assert out["list"] == ["29019", "20091"]
        # A county frames its whole state.
        assert out["county"] == ["29019", "29095"]
        assert out["state"] == ["29019", "29095"]
        assert out["auto"] == ["17001"]
        assert out["listWins"] == ["17001"]

    def test_the_story_map_frames_by_county_and_the_flow_map_by_feature(self):
        source = CHART.read_text()
        story = source[source.index("  function renderStoryMap(") :]
        story = story[: story.index("\n  }\n")]
        flow = source[source.index("  function renderFlowMap(") :]
        flow = flow[: flow.index("\n  }\n")]
        assert "(f) => String(f.id).slice(0, 5)" in story
        assert "(f) => String(f.id)," in flow
        # Neither has its own copy of the three branches any more.
        for body in (story, flow):
            assert body.count("/^\\d{5}$/.test(focus)") == 0


# --- one swatch, one legend, one font, one svg root --------------------------


class TestOneLegendBuilder:
    def test_a_swatch_item_is_a_colour_and_a_label(self):
        script = "\n".join(
            [
                "const made = [];",
                "global.document = { createElement: (tag) => {",
                "  const node = { tag, className: '', style: {}, kids: [],",
                "    append(...k) { this.kids.push(...k); },",
                "    appendChild(k) { this.kids.push(k); } };",
                "  made.push(node); return node;",
                "} };",
                "const plain = T.swatchItem('none', '#eee');",
                "const keyed = T.swatchItem('Sports', '#123456', {",
                "  item: 'dd-key-item', swatch: 'dd-key-swatch',",
                "  label: 'dd-key-label' });",
                "const hatched = T.swatchItem('too uncertain', null,",
                "  { swatch: 'dd-swatch hatched' });",
                "const whole = T.legend([['a', '#111'], ['b', '#222']]);",
                "console.log(JSON.stringify({",
                "  plainSwatch: plain.swatch.className,",
                "  plainColour: plain.swatch.style.background,",
                "  plainKids: plain.item.kids.length,",
                "  keyedItem: keyed.item.className,",
                "  keyedLabel: keyed.item.kids[1].className,",
                "  keyedText: keyed.item.kids[1].textContent,",
                "  hatchedSwatch: hatched.swatch.className,",
                "  hatchedColour: hatched.swatch.style.background === undefined,",
                "  legendClass: whole.className, legendKids: whole.kids.length,",
                "}));",
            ]
        )
        # The stand-in `document` records what is built; the runtime reads
        # the global when it builds, so it is the one used.
        out = run(script, libs=())
        assert out["plainSwatch"] == "dd-swatch"
        assert out["plainColour"] == "#eee"
        # A swatch and its text, with no wrapper span unless one is asked for.
        assert out["plainKids"] == 2
        assert out["keyedItem"] == "dd-key-item"
        assert out["keyedLabel"] == "dd-key-label"
        assert out["keyedText"] == "Sports"
        # The hatched swatch is styled by its class, not by a colour.
        assert out["hatchedSwatch"] == "dd-swatch hatched"
        assert out["hatchedColour"]
        assert out["legendClass"] == "dd-legend" and out["legendKids"] == 2

    def test_the_two_named_legends_go_through_it(self):
        source = CHART.read_text()
        for name in ("swatchLegend", "htmlLegend"):
            body = _function(source, name)
            assert "legend(" in body, name
            assert "createElement" not in body, name
        # The bar chart's key keeps its own classes.
        key = _function(source, "swatchLegend")
        for cls in ("dd-legend dd-key", "dd-key-item", "dd-key-swatch", "dd-key-label"):
            assert cls in key, cls

    def test_the_font_is_named_once(self):
        source = CHART.read_text()
        assert source.count("system-ui") == 1
        assert re.search(r"^  const FONT = 'system-ui, -apple-system", source, re.M)
        # And every chart that sets a face reads it.
        assert source.count("FONT") >= 4

    def test_both_maps_start_from_the_shared_svg_root(self):
        source = CHART.read_text()
        # svgRoot itself, and the sankey, which sizes itself by how many
        # rows it has and sets its face as attributes rather than style.
        assert source.count('d3.create("svg")') == 2
        assert source.count("svgRoot(width, height, t, { centred: false })") == 2
        root = _function(source, "svgRoot")
        assert "centred" in root and "display:block;" in root

    def test_a_centred_root_still_centres(self):
        script = "\n".join(
            [
                "const calls = {};",
                "const node = { attr(k, v) { calls[k] = v; return this; } };",
                "global.d3 = { create: () => node };",
                "const t = { ink: '#111' };",
                "T.svgRoot(400, 200, t);",
                "const centred = { box: calls.viewBox, style: calls.style };",
                "T.svgRoot(400, 200, t, { centred: false });",
                "console.log(JSON.stringify({ centred, font: T.FONT,",
                "  corner: { box: calls.viewBox, style: calls.style } }));",
            ]
        )
        out = run(script, libs=())
        assert out["centred"]["box"] == [-200, -100, 400, 200]
        assert "color:#111" in out["centred"]["style"]
        assert f"font-family:{out['font']}" in out["centred"]["style"]
        assert "display:block" not in out["centred"]["style"]
        assert out["corner"]["box"] == [0, 0, 400, 200]
        assert "display:block" in out["corner"]["style"]
        assert "color:" not in out["corner"]["style"]
