"""Three findings of the 2026-09-30 review on the chart runtime's hot paths.

- Every pointer move over a tract restyled all 1,654 paths and rewrote
  the tooltip's HTML; now siblings dim on entering a mark and the HTML is
  rewritten when the datum changes.
- Every redraw decoded the topology into features again and repeated
  the coincident-dot spread; both are remembered.
- The flow map's route search inverted every trace point once per
  county, inside a SLIDE x CURVE x SIDE x RUN loop; each point is
  inverted once and only counties whose bounds hold it are asked.

The pure functions run in node; the DOM parts are pinned by text.
"""

from pathlib import Path

from tests.chart_runtime import run

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "static/js/datadesk-chart.js"


def _function(source, name):
    start = source.index(f"function {name}(")
    return source[start : source.index("\n  }", start) + 4]


# --- hover ---------------------------------------------------------------------


class TestHoverIsCheap:
    def test_siblings_dim_on_enter_not_on_every_move(self):
        source = CHART.read_text()
        body = _function(source, "interactive")
        assert '"pointerenter pointermove"' not in body
        assert '.on("pointerenter", function (event, d) {' in body
        assert '.on("pointermove", (event) => tip.move(event))' in body
        enter = body.index('.on("pointerenter"')
        move = body.index('.on("pointermove"')
        assert "isolate(d)" in body[enter:move]
        assert "isolate" not in body[move : body.index('.on("pointerleave"')]

    def test_the_tooltip_rewrites_html_only_when_it_changes(self):
        source = CHART.read_text()
        start = source.index("function tooltip(")
        body = source[start : source.index("\n  }\n", start)]
        assert "let last = null;" in body
        assert "if (html !== last) { node.innerHTML = html; last = html; }" in body
        assert body.count("innerHTML = ") == 1


# --- features and the spread are remembered ----------------------------------


class TestARedrawDoesNotRedoTheMap:
    def test_features_are_decoded_once_per_file_and_object(self):
        script = "\n".join(
            [
                "let decoded = 0;",
                "global.topojson = { feature: (topo, object) => {",
                "  decoded += 1;",
                "  return { features: [{ id: null, properties: { GEOID: '29' } }] };",
                "} };",
                "const topo = { objects: { counties: {}, states: {} } };",
                "const a = T.featuresOf('c.json', topo, 'counties');",
                "const b = T.featuresOf('c.json', topo, 'counties');",
                "const c = T.featuresOf('c.json', topo, 'states');",
                "const d = T.featuresOf('s.json', topo, 'states');",
                "console.log(JSON.stringify({",
                "  decoded, same: a === b, id: a[0].id,",
                "  distinct: c !== a && d !== c }));",
            ]
        )
        # The stand-in `topojson` counts decodes; the runtime reads the
        # global at call time, so it is the one used.
        out = run(script, libs=())
        assert out["same"] and out["distinct"]
        assert out["decoded"] == 3
        assert out["id"] == "29"

    def test_boundaries_decode_through_the_cache(self):
        source = CHART.read_text()
        body = _function(source, "boundaries")
        assert "featuresOf(url, topo, spec.object)" in body
        assert "featuresOf(`${base}${spec.perState}${s}.json`, topo, level)" in body
        assert "toFeatures(" not in body

    def test_the_spread_is_remembered_for_the_same_points(self):
        script = "\n".join(
            [
                "const xy = [[100, 100], [100, 100], [140, 90]];",
                "const one = T.spreadCoincident(xy, 3.5);",
                "const two = T.spreadCoincident(xy.map((p) => p.slice()), 3.5);",
                "const other = T.spreadCoincident(xy, 4);",
                "const fresh = T.spreadPoints(xy, 3.5);",
                "console.log(JSON.stringify({ same: one === two, other: other !== one,",
                "  equal: JSON.stringify(one) === JSON.stringify(fresh) }));",
            ]
        )
        out = run(script)
        # Equal points at the same radius: the same answer, not recomputed.
        assert out["same"] and out["other"] and out["equal"]

    def test_the_story_map_still_calls_it_the_same_way(self):
        # tests/test_coincident_newsrooms_are_spread.py pins this line too.
        assert (
            "const xy = byCategory ? spreadCoincident(at, dotR) : at;"
            in CHART.read_text()
        )


# --- one redraw per frame ------------------------------------------------------


class TestResizeRedrawsOncePerFrame:
    def test_the_observer_waits_for_a_frame(self):
        source = CHART.read_text()
        body = source[source.index("function mount(") :]
        body = body[: body.index("\n  }\n")]
        assert "if (frame) return;" in body
        assert "frame = requestAnimationFrame(() => {" in body
        # The measurement happens in the frame, not in the observation.
        raf = body.index("requestAnimationFrame")
        assert body.index("roomFor(el);", raf) > raf
        after = body[body.index("destroy()") :]
        assert "if (frame) cancelAnimationFrame(frame);" in after


# --- the route search ----------------------------------------------------------


class TestTheRouteSearchInvertsOnce:
    def test_points_are_inverted_once_and_counties_prefiltered(self):
        source = CHART.read_text()
        start = source.index("      const trespass = (trace, x, y) => {")
        body = source[start : source.index("\n      };", start)]
        assert ".map((p) => projection.invert([p.x, p.y])).filter(Boolean)" in body
        assert "for (const geoid of subjects)" in body
        assert "within(bounds, ll) && d3.geoContains(shape, ll)" in body
        # No inversion inside the county loop.
        loop = body[body.index("for (const geoid of subjects)") :]
        assert "projection.invert" not in loop

    def test_bounds_are_computed_once_per_subject(self):
        source = CHART.read_text()
        assert "const subjects = [...shapeOf.keys()].filter(isSubject);" in source
        assert "const boundsOf = new Map(subjects.map(" in source
        assert "d3.geoBounds(shapeOf.get(geoid))" in source

    def test_within_reads_a_geo_bounds_pair(self):
        source = CHART.read_text()
        start = source.index("      const within = ")
        within = source[start : source.index(";\n", start) + 1]
        script = (
            within
            + "\nconsole.log(JSON.stringify(["
            + "within([[-95, 36], [-89, 40]], [-92, 38]),"
            + "within([[-95, 36], [-89, 40]], [-88, 38]),"
            + "within([[-95, 36], [-89, 40]], [-92, 41])]));"
        )
        # A local of the flow map's route search, so it is lifted out; the
        # runtime loads beside it and is not used.
        assert run(script, libs=()) == [True, False, False]
