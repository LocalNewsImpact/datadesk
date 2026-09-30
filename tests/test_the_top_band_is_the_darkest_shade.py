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

import json
from pathlib import Path

import pytest

from tests.chart_runtime import value

TYPES = Path(__file__).resolve().parent.parent / "visuals/types.py"
BUILDER = Path(__file__).resolve().parent.parent / "visuals/builder.py"

#: Counties holding small, tied numbers: ten deciles collapse to six cuts.
TIED = [1] * 8 + [2] * 6 + [3] * 4 + [4, 4, 5, 6, 7, 9, 12, 18, 25, 40, 88, 204]
#: Counties spread evenly: every one of the nine cuts survives.
SPREAD = list(range(15, 975, 32))


def _bands(values, config=None):
    """Ask the renderer to band `values` as a story map would."""
    return value(f"""(() => {{
          const t = T.theme();
          const values = {json.dumps(sorted(values))};
          const b = T.storyMapBands(values, {json.dumps(config or {})}, null, t);
          return {{
            cuts: b.cuts,
            ramp: b.ramp,
            seqHigh: t.seqHigh,
            bands: values.map(b.bandOf),
            shades: values.map(b.shadeFor),
            labels: b.bandLabels,
          }};
        }})()""")


class TestTheRampIsSizedAfterTheCuts:
    @pytest.mark.parametrize("values,cuts", [(TIED, 6), (SPREAD, 9)])
    def test_the_darkest_county_is_the_darkest_blue(self, values, cuts):
        """Whether ten deciles survive as six cuts or nine, the county at
        the top is drawn in `seqHigh` -- the property the ramp was resized
        for. Sized from `steps` it topped out at a mid-tone."""
        b = _bands(values)
        assert len(b["cuts"]) == cuts
        assert b["shades"][-1] == b["seqHigh"]

    @pytest.mark.parametrize("values", [TIED, SPREAD])
    def test_every_band_has_a_shade_and_every_shade_a_band(self, values):
        """One shade per band plus the empty band: none unreachable."""
        b = _bands(values)
        assert len(b["ramp"]) == len(b["cuts"]) + 2
        assert max(b["bands"]) == len(b["ramp"]) - 1

    def test_tied_counts_share_a_shade(self):
        b = _bands(TIED)
        assert len({b["shades"][i] for i in range(8)}) == 1


class TestAbsoluteBanding:
    """Relative shading is right for a map read alone and wrong for two maps
    read together: both top out at the same dark blue whether the county
    behind it holds fifteen stories or two hundred."""

    def test_the_ladder_is_the_cuts_whatever_the_values(self):
        ladder = [1, 2, 5, 10, 20, 50, 100, 200, 500]
        assert _bands(TIED, {"band_scale": "absolute"})["cuts"] == ladder
        assert _bands(SPREAD, {"band_scale": "absolute"})["cuts"] == ladder

    def test_one_count_is_one_shade_on_every_map(self):
        """The point of the setting. 40 stories is drawn the same on a map
        of small counties and a map of large ones."""
        small = _bands(TIED, {"band_scale": "absolute"})
        large = _bands(SPREAD + [40], {"band_scale": "absolute"})
        assert (
            small["shades"][sorted(TIED).index(40)]
            == large["shades"][sorted(SPREAD + [40]).index(40)]
        )

    def test_relative_is_the_default_and_follows_the_values(self):
        """An unset `band_scale` keeps the behaviour every existing visual
        was built with: the cuts are this map's own quantiles."""
        assert _bands(TIED)["cuts"] != _bands(SPREAD)["cuts"]
        assert _bands(TIED)["cuts"] == _bands(TIED, {"band_scale": ""})["cuts"]

    def test_the_top_band_says_where_it_ends(self):
        """ "12+" hid a tail running to 204."""
        assert _bands(TIED)["labels"][-1].endswith("204")


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
