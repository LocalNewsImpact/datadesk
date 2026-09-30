"""Three findings of the 2026-09-30 review on the chart runtime's mount.

- A resize or a theme change redrew from scratch: an open table view (and
  its "View chart" button) was replaced by the chart, and a layered map
  came back on its first layer with the newsrooms on. `mount` now keeps a
  mode and a state that outlive the renderer, and returns `destroy()`.
- The site's own theme toggle stamps `data-theme` on <html>, which the
  media query never saw; `mount` watches the stamp.
- SVG ids are minted by one counter, so two charts on a page cannot share
  a clip path, a hatch pattern or a chord's text paths.

The runtime's pure parts run in node; the DOM parts are pinned by text.
"""

from pathlib import Path

from tests.chart_runtime import run, value

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "static/js/datadesk-chart.js"
BUILDER = ROOT / "templates/visuals/renderers/builder.html"


def _mount_body():
    source = CHART.read_text()
    body = source[source.index("function mount(") :]
    return body[: body.index("\n  }\n")]


# --- the layered map remembers ------------------------------------------------


class TestTheLayeredMapRemembersItsChoices:
    def test_the_state_decides_the_layer_and_the_points(self):
        script = "\n".join(
            [
                "const choices = [",
                "  { id: 'coverage' }, { id: 'median_age' }, { id: 'none' }];",
                "const points = [{ name: 'KOMU' }];",
                "console.log(JSON.stringify({",
                "  fresh: T.layerState({}, choices, points),",
                "  fresh_no_points: T.layerState({}, choices, []),",
                "  kept: T.layerState(",
                "    { active: 'median_age', showPoints: false }, choices, points),",
                "  unknown: T.layerState(",
                "    { active: 'gone', showPoints: true }, choices, []),",
                "}));",
            ]
        )
        out = run(script)
        # First draw: the first choice, newsrooms on when there are any.
        assert out["fresh"] == {"active": "coverage", "showPoints": True}
        assert out["fresh_no_points"] == {"active": "coverage", "showPoints": False}
        # A redraw: what the reader chose.
        assert out["kept"] == {"active": "median_age", "showPoints": False}
        # A remembered layer the payload no longer carries falls back; the
        # toggle is kept as set.
        assert out["unknown"] == {"active": "coverage", "showPoints": True}

    def test_the_renderer_writes_its_choices_back(self):
        source = CHART.read_text()
        body = source[source.index("function renderLayerMap(") :]
        body = body[: body.index("\n  function ")]
        assert "const state = (opts && opts.state) || {};" in body
        assert "layerState(state, choices, points)" in body
        assert "active = state.active = c.id;" in body
        assert "showPoints = state.showPoints = box.checked;" in body
        assert "Math.random()" not in body


# --- the mount keeps a mode and a state ---------------------------------------


class TestTheMountKeepsTheReadersPlace:
    def test_a_table_is_left_alone_by_a_redraw(self):
        body = _mount_body()
        assert 'if (mode === "table") return;' in body
        assert 'table(show) { mode = "table"; show(); }' in body
        assert 'redraw() { mode = "chart"; draw(); }' in body

    def test_the_state_reaches_the_renderer(self):
        body = _mount_body()
        assert "const state = {};" in body
        assert "render(el, config, rows, { ...(opts || {}), state });" in body

    def test_the_sites_theme_toggle_redraws(self):
        body = _mount_body()
        assert "new MutationObserver(draw)" in body
        assert 'attributeFilter: ["data-theme"]' in body
        assert "stamps.observe(document.documentElement" in body

    def test_destroy_lets_go_of_everything(self):
        body = _mount_body()
        after = body[body.index("destroy()") :]
        assert 'media.removeEventListener("change", draw)' in after
        assert "stamps.disconnect()" in after
        assert "sizes.disconnect()" in after

    def test_the_builder_page_draws_its_table_through_the_mount(self):
        page = BUILDER.read_text()
        assert "if (chart) chart.table(show); else show();" in page
        assert "renderTable(el, data, credits, takeaway, toggle)" in page


# --- ids are minted, never derived --------------------------------------------


class TestEveryChartMintsItsOwnIds:
    def test_uid_never_repeats(self):
        ids = value('[T.uid("dd-clip"), T.uid("dd-clip"), T.uid("dd-hatch")]')
        assert len(set(ids)) == 3
        assert ids[0].startswith("dd-clip-") and ids[2].startswith("dd-hatch-")

    def test_the_story_map_and_the_chord_use_it(self):
        source = CHART.read_text()
        assert 'const clipId = uid("dd-clip");' in source
        assert 'const hatchId = uid("dd-hatch");' in source
        assert 'const chordId = uid("dd-chord");' in source
        assert "hashOf" not in source
        assert '"dd-clip-" + Math.abs(width' not in source
