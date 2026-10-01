"""A newsroom map's legend draws the ring each kind of dot wears.

When the story map's legend was lifted into `storyMapLegend` (#436), the
renderer kept handing it `outline`, but the function never took it out of
its argument. Every map that colours its dots by newsroom kind -- Missouri
Newsrooms among them -- stopped at the legend with "outline is not
defined", and the page showed "This chart could not be drawn".
"""

import pytest

from tests.chart_runtime import run

_DOCUMENT = "\n".join(
    [
        "global.document = {",
        "  documentElement: { dataset: { theme: 'light' } },",
        "  addEventListener() {}, querySelectorAll: () => [],",
        "  createElement: (tag) => ({ tag, className: '', style: {}, kids: [],",
        "    dataset: {},",
        "    append(...k) { this.kids.push(...k); },",
        "    appendChild(k) { this.kids.push(k); },",
        "    prepend(k) { this.kids.unshift(k); } }),",
        "  createTextNode: (text) => ({ text }),",
        "};",
    ]
)


def _legend(outline):
    script = "\n".join(
        [
            _DOCUMENT,
            "const t = T.theme();",
            "const categories = ['Newspaper', 'Radio'];",
            "const colours = { Newspaper: '#1baf7a', Radio: '#2b5fd9' };",
            "const legend = T.storyMapLegend(t, {",
            "  byCategory: true, categories,",
            "  placed: [{ category: 'Newspaper' }, { category: 'Radio' }],",
            "  colourOf: (c) => colours[c],",
            f"  outline: {outline!r},",
            "  max: 0,",
            "});",
            "const swatches = legend.kids.map((item) => item.kids[0]);",
            "console.log(JSON.stringify({",
            "  items: legend.kids.length,",
            "  labels: legend.kids.map((item) => item.kids[1]),",
            "  rings: swatches.map((s) => s.style.boxShadow || null),",
            f"  rows: categories.map((c) => T.newsroomRing(t, c, {outline!r}).inked),",
            "}));",
        ]
    )
    return run(script)


class TestTheNewsroomLegend:
    def test_it_draws_without_a_reference_error(self):
        out = _legend("thin")
        assert out["items"] == 2
        assert out["labels"] == ["Newspaper", "Radio"]

    def test_a_thin_outline_rings_every_key(self):
        out = _legend("thin")
        assert out["rings"] == ["inset 0 0 0 0.5px #161616"] * 2

    def test_no_outline_rings_no_key(self):
        out = _legend("none")
        assert out["rings"] == [None, None]

    @pytest.mark.parametrize("outline", ["thin", "none", "default"])
    def test_the_key_matches_the_dot(self, outline):
        """The key wears a ring exactly when the dot on the map does."""
        out = _legend(outline)
        assert [ring is not None for ring in out["rings"]] == out["rows"]
