"""A shaded county's edge is its own fill, shifted -- not the boundary grey.

The grey sat close to the ramp's pale end, so neighbouring counties in the
same band merged: 63 of 115 counties on the outlet map shared one shade and
read as one shape. A white hairline separated them and overwhelmed the map;
the fill a step lighter (light mode) or darker (dark mode) marks the edge
and keeps the colours (2026-09-28).
"""

from tests.test_a_table_is_a_pivot import CHART

JS = CHART.read_text()


def test_a_shaded_county_is_edged_in_its_own_colour():
    assert '.attr("stroke", (f) => countyEdge(byCounty.get(String(f.id))))' in JS
    assert "return (lightMode ? c.brighter(0.35) : c.darker(0.35)).formatHex();" in JS


def test_a_county_with_nothing_keeps_the_grey():
    assert "if (!n) return t.boundary;" in JS
