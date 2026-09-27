"""Stacked bars that read as one set of colours, separated, with a key.

The CIN palette was ten full-chroma hues that read as neon beside the maps'
muted ramps; in dark mode the half-pixel gap between segments showed the
page through it as a black rule, and Plot's legend packed labels against
their swatches. Chosen against the live CIN chart in both modes, 2026-09-27.
"""

import re

from tests.test_a_table_is_a_pivot import CHART, _node

JS = CHART.read_text()

#: Tableau's Color Blind 10 as published, in its own order.
TABLEAU_COLOR_BLIND = [
    "#1170aa",
    "#a3acb9",
    "#c85200",
    "#a3cce9",
    "#57606c",
    "#fc7d0b",
    "#c8d0d9",
    "#7b848f",
    "#ffbc79",
    "#5fa2ce",
]


def _cin(mode):
    block = re.search(r"cin: \{.*?light: \[(.*?)\],\s*dark: \[(.*?)\],", JS, re.S)
    return re.findall(r'"(#[0-9a-f]{6})"', block.group(1 if mode == "light" else 2))


class TestThePalette:
    def test_light_is_tableau_color_blind_as_published(self):
        assert _cin("light") == TABLEAU_COLOR_BLIND

    def test_dark_is_its_families_with_two_greys(self):
        """On the dark surface the published set's four pale greys and
        grey-blues blurred together; dark keeps the blues and oranges."""
        dark = _cin("dark")
        assert len(dark) == 10 and len(set(dark)) == 10
        greys = {"#a3acb9", "#57606c", "#c8d0d9", "#7b848f"}
        assert len(greys & set(dark)) == 2


class TestTheDivider:
    def test_a_stack_is_divided_by_a_hairline_not_a_gap(self):
        assert "{ inset: 0, stroke: t.surface, strokeWidth: 0.5 }" in JS
        assert '{ inset: 0, stroke: "#5c5c58", strokeWidth: 1 }' in JS

    def test_side_by_side_bars_keep_their_gap(self):
        assert "? { inset: 0.5 }" in JS


class TestTheKey:
    def test_plot_draws_no_legend(self):
        assert "color: color ? { ...color, legend: false } : color," in JS
        assert "if (keyed) el.prepend(swatchLegend(color.domain, color.range));" in JS

    def test_one_item_per_category_in_order(self):
        """A key built in node with a stand-in document: one swatch and one
        label per category, in the chart's order."""
        got = _node("""(() => {
              const made = [];
              const node = (tag) => {
                const n = { tag, className: "", style: {}, children: [],
                  textContent: "", append(...c) { this.children.push(...c); },
                  appendChild(c) { this.children.push(c); } };
                made.push(n); return n;
              };
              global.document.createElement = node;
              const key = T.swatchLegend(["Health", "Sports"], ["#a", "#b"]);
              return key.children.map((item) => [
                item.children[0].style.background, item.children[1].textContent]);
            })()""")
        assert got == [["#a", "Health"], ["#b", "Sports"]]
