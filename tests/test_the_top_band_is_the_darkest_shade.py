"""A map's darkest county is the darkest blue, on every map.

The story map's ramp was sized from `steps` -- the number of bands asked
for -- while `bandOf` can only ever return `cuts.length + 1`. Quantile cuts
de-duplicate, because a tied count puts two quantiles on the same number, so
ten deciles over counties holding 1,1,1,2,2,4 survive as six distinct cuts,
not nine.

The ramp then held shades no band could reach. A map of small tied numbers
topped out at a mid-tone while a map of large spread numbers reached the
darkest blue, and the two could not be compared -- the one with the bigger
numbers looked lighter.
"""

from pathlib import Path

import pytest

CHART_JS = Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"
TYPES = Path(__file__).resolve().parent.parent / "visuals/types.py"
BUILDER = Path(__file__).resolve().parent.parent / "visuals/builder.py"


def _storymap() -> str:
    source = CHART_JS.read_text()
    start = source.index("function renderStoryMap")
    return source[start : source.index("function ", start + 20)]


class TestTheRampIsSizedAfterTheCuts:
    def test_it_is_built_from_the_cuts_and_not_the_steps(self):
        body = _storymap()
        assert "quantizeRamp(t.seqLow, t.seqHigh, cuts.length + 2)" in body

    def test_the_old_sizing_is_gone(self):
        """`steps + 1` is the bug: it counts bands that were asked for, not
        bands that exist after de-duplication."""
        assert "quantizeRamp(t.seqLow, t.seqHigh, steps + 1)" not in _storymap()

    def test_the_ramp_is_built_after_the_cuts_are_known(self):
        body = _storymap()
        assert body.index("const cuts =") < body.index("const ramp =")


class TestAbsoluteBanding:
    """Relative shading is right for a map read alone and wrong for two maps
    read together: both top out at the same dark blue whether the county
    behind it holds fifteen stories or two hundred."""

    def test_the_ladder_exists_and_is_logarithmic(self):
        source = CHART_JS.read_text()
        assert "const ABSOLUTE_BANDS = [1, 2, 5, 10, 20, 50, 100, 200, 500]" in source

    def test_absolute_uses_the_ladder_instead_of_quantiles(self):
        body = _storymap()
        assert 'config.band_scale === "absolute"' in body
        assert "? ABSOLUTE_BANDS" in body

    def test_relative_is_the_default(self):
        """An unset `band_scale` keeps the behaviour every existing visual
        was built with."""
        body = _storymap()
        cut = body.index('config.band_scale === "absolute"')
        assert "d3.quantile" in body[cut:]


class TestTheSettingReachesTheBuilderAndSurvivesASave:
    def test_it_is_offered_in_the_builder(self):
        source = TYPES.read_text()
        assert '"band_scale"' in source
        assert "Compare shading with other maps" in source

    def test_both_choices_are_named(self):
        source = TYPES.read_text()
        assert '("", "Relative' in source
        assert '("absolute", "Absolute' in source

    def test_it_is_persisted(self):
        """An option missing from the builder's key list renders, takes an
        answer and silently discards it on save -- which is how `bands`
        itself was once declared and unreachable."""
        assert '"band_scale",' in BUILDER.read_text()

    def test_it_is_persisted_as_a_string_not_a_flag(self):
        """It has three states over time, not two, so it must not join
        `_BOOL_KEYS`."""
        source = BUILDER.read_text()
        bools = source[source.index("_BOOL_KEYS") : source.index("_BOOL_KEYS") + 200]
        assert "band_scale" not in bools


@pytest.mark.parametrize(
    "values,steps,expected_cuts",
    [
        # Tied and low: deciles collapse, and the ramp must collapse with them.
        (
            [1] * 8 + [2] * 6 + [3] * 4 + [4, 4, 5, 6, 7, 9, 12, 18, 25, 40, 88, 204],
            10,
            6,
        ),
        # Spread: every cut survives.
        (list(range(15, 975, 32)), 10, 9),
    ],
)
def test_the_top_band_is_always_the_last_ramp_index(values, steps, expected_cuts):
    """The maths the renderer does, in Python, so the property is pinned
    rather than eyeballed in a browser."""
    ordered = sorted(values)

    def quantile(p):
        i = (len(ordered) - 1) * p
        lo, hi = int(i), min(int(i) + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (i - lo)

    cuts: list[int] = []
    for i in range(steps - 1):
        cut = max(1, round(quantile((i + 1) / steps)))
        if not cuts or cut > cuts[-1]:
            cuts.append(cut)
    assert len(cuts) == expected_cuts

    def band_of(n):
        for index, cut in enumerate(cuts):
            if n <= cut:
                return index + 1
        return len(cuts) + 1

    ramp_length = len(cuts) + 2
    assert band_of(max(ordered)) == ramp_length - 1
