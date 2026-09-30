"""Four bands over a skewed count is not a ranking.

The Missouri story map's top band read "12+" and held every county from
12 stories to 2,093 in one colour. Measured on March 2026, county-level
geoids: 115 counties, median 74, 75th percentile 142, maximum 2,093 --
so three quarters of the RANGE sat in the darkest band and a county with
fifteen stories was drawn the same as Boone.

TWO CAUSES, and only one of them was the band count.

The `bands` option existed, was labelled "Shading steps", and carried the
note "Read by the renderer; no control offers it yet." It had no
`values`, and a choice with no values renders an empty control -- so the
setting was declared, documented as unreachable, and left that way.

The other was the ramp. `quantizeRamp` stepped the hex channels
linearly, which does not step the eye linearly: on the four themes the
worst adjacent pair at ten bands differed by 0.024 of relative
luminance, while the best differed by three times that. Spacing the
steps by perceived lightness instead -- same two endpoints, same
straight line between them, only the parameterisation changed -- makes
every pair about 0.071, which is more separation than FOUR bands had
before.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.chart_runtime import value

CHART = Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"


def _luminance(hex_colour):
    """WCAG relative luminance, which is what a reader's eye tracks and
    what a greyscale print keeps."""
    h = hex_colour.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _palettes():
    """Every distinct sequential pair the runtime's themes carry, by mode:
    `{"light": [[low, high], ...], "dark": [...]}`. Read from `THEMES`
    itself, so a theme added tomorrow is covered without a regex to
    update."""
    return value("""(() => {
          const out = { light: [], dark: [] };
          for (const modes of Object.values(T.THEMES)) {
            for (const mode of ["light", "dark"]) {
              const pair = [modes[mode].seqLow, modes[mode].seqHigh];
              if (!out[mode].some((p) => p[0] === pair[0] && p[1] === pair[1]))
                out[mode].push(pair);
            }
          }
          return out;
        })()""")


def _ramps(bands, mode=None):
    """The SHIPPED `quantizeRamp`, run for every theme's sequential ramp."""
    palettes = _palettes()
    pairs = palettes[mode] if mode else palettes["light"] + palettes["dark"]
    return value(f"{json.dumps(pairs)}.map(([a, b]) => T.quantizeRamp(a, b, {bands}))")


def _bands(values, config=None):
    return value(f"""(() => {{
          const b = T.storyMapBands(
            {json.dumps(sorted(values))}, {json.dumps(config or {})}, null, T.theme());
          return {{ steps: b.steps, cuts: b.cuts, ramp: b.ramp, labels: b.bandLabels }};
        }})()""")


#: Enough counties that the renderer takes quantiles rather than the
#: fixed cuts it falls back to under `steps * 2` values.
SPREAD = list(range(3, 243, 2))


class TestTheOptionCanBeReached:
    def test_it_has_values(self):
        """THE REASON IT DID NOTHING. A choice with no values renders an
        empty control."""
        source = (
            Path(__file__).resolve().parent.parent / "visuals/types.py"
        ).read_text()
        block = source[source.index('"bands",') :]
        block = block[: block.index("),\n        ),")]
        assert "values=(" in block
        assert block.count('("') > 3

    def test_it_no_longer_claims_to_be_unreachable(self):
        source = (
            Path(__file__).resolve().parent.parent / "visuals/types.py"
        ).read_text()
        assert "no control offers it yet" not in source

    def test_the_default_is_deciles(self):
        """Ten, not four. The empty value is what a visual that has never
        been told anything gets."""
        source = (
            Path(__file__).resolve().parent.parent / "visuals/types.py"
        ).read_text()
        block = source[source.index('"bands",') :]
        assert '("", "10' in block[: block.index("),\n        ),")]


class TestTheRendererReadsIt:
    def test_the_step_count_comes_from_the_config(self):
        assert _bands(SPREAD, {"bands": "7"})["steps"] == 7
        assert len(_bands(SPREAD, {"bands": "7"})["cuts"]) == 6

    def test_it_falls_back_to_ten(self):
        assert _bands(SPREAD)["steps"] == 10
        assert _bands(SPREAD, {"bands": "many"})["steps"] == 10

    def test_it_is_capped_and_floored(self):
        """Twelve is where the ramp stops separating; three is the fewest
        that is still a scale."""
        assert _bands(SPREAD, {"bands": "40"})["steps"] == 12
        assert _bands(SPREAD, {"bands": "1"})["steps"] == 3

    def test_fixed_still_means_the_march_cuts(self):
        assert _bands(SPREAD, {"bands": "fixed"})["cuts"] == [2, 5, 9]

    def test_the_ramp_and_the_legend_are_the_same_length(self):
        """A ramp shorter than the band count paints the map `undefined`;
        a ramp LONGER than it holds shades no band can reach.

        It was `steps + 1`, the count asked for. `bandOf` returns at most
        `cuts.length + 1`, and quantile cuts de-duplicate -- ten deciles
        over a tied count survive as six -- so the darkest shades went
        unpainted and a map of small numbers topped out lighter than a map
        of large ones. Both are `cuts.length + 2` now: a zero band, one per
        cut, and the tail above the last cut."""
        tied = [1] * 8 + [2] * 6 + [3] * 4 + [4, 4, 5, 6, 7, 9, 12, 18, 25, 40, 88, 204]
        for values in (SPREAD, tied):
            b = _bands(values)
            assert len(b["ramp"]) == len(b["labels"]) == len(b["cuts"]) + 2


class TestTheTopBandSaysWhereItEnds:
    def test_it_names_the_maximum(self):
        """ "12+" hides the whole tail: a reader cannot tell whether the
        darkest county holds 13 stories or 2,093."""
        top = _bands(SPREAD + [2093])["labels"][-1]
        assert top.endswith("2093") and "+" not in top

    def test_a_single_valued_top_band_is_not_a_range(self):
        """`2093–2093` is not a label."""
        labels = _bands([1, 2, 5, 9, 10], {"bands": "fixed"})["labels"]
        assert labels[-1] == "10"


class TestTheRampSeparatesAtTenBands:
    """Measured on what ships, for every ramp in the file.

    LIGHT AND DARK WERE NOT THE SAME PROBLEM, and now they are. The dark
    ramps spanned 0.481 to 0.611 of relative luminance against light's
    0.706 to 0.770, so the same number of bands separated about a third
    less in dark. The pale ends were extended along their own hue lines
    on 2026-09-14 -- the room was all there, because the dark ends
    already sit near the surface -- and both families now clear the same
    floor. It is still asserted per family, so a palette change to one
    cannot hide behind the other.
    """

    def _gaps(self, ramp):
        swatches = ramp[1:]  # index 0 is the unused zero slot
        return [
            abs(_luminance(swatches[i]) - _luminance(swatches[i + 1]))
            for i in range(len(swatches) - 1)
        ]

    def _by_mode(self, bands):
        return _ramps(bands, "light"), _ramps(bands, "dark")

    def test_light_separates_as_well_at_ten_as_it_did_at_four(self):
        """The claim the change rests on. The old sRGB four-band light
        ramp's worst pair was 0.077; ten even bands hold 0.068 -- the
        same readability at two and a half times the resolution."""
        light, _ = self._by_mode(11)
        for ramp in light:
            assert min(self._gaps(ramp)) > 0.065, ramp

    def test_dark_separates_as_well_as_light(self):
        """IT DID NOT, AND THAT WAS THE PALETTE. A dark-mode ramp runs
        dark to pale and these stopped short -- 0.481 to 0.611 of
        luminance against light's 0.706 to 0.770 -- so ten bands
        separated at 0.045 where light managed 0.068 and the same map was
        harder to read in dark mode.

        The pale ends were extended along their own hue lines on
        2026-09-14. Held to light's floor now, because there is no longer
        a reason for them to differ."""
        _, dark = self._by_mode(11)
        for ramp in dark:
            assert min(self._gaps(ramp)) > 0.065, ramp

    def test_twelve_is_still_readable_in_light(self):
        """The cap, justified rather than guessed."""
        light, _ = self._by_mode(13)
        for ramp in light:
            assert min(self._gaps(ramp)) > 0.055, ramp

    def test_twelve_is_still_readable_in_dark(self):
        """The cap holds in both modes now. It was 0.038 in dark before
        the ramps were widened, which is why twelve is a cap and not a
        default; it is 0.056 now, the same as light."""
        _, dark = self._by_mode(13)
        for ramp in dark:
            assert min(self._gaps(ramp)) > 0.055, ramp

    def test_the_steps_are_even_rather_than_bunched(self):
        """THE WHOLE POINT. Linear sRGB steps put the biggest jumps in
        the middle and the smallest at the ends, so the worst pair was a
        third of the best and the palest bands merged."""
        for ramp in _ramps(11):
            gaps = self._gaps(ramp)
            assert max(gaps) / min(gaps) < 1.35, f"uneven: {gaps}"

    def test_every_ramp_beats_what_it_replaced_at_the_same_count(self):
        """Light and dark both. The old ramp at ten bands gave 0.024 to
        0.034 depending on the theme; nothing here may be worse than
        0.040."""
        for ramp in _ramps(11):
            assert min(self._gaps(ramp)) > 0.040, ramp

    def test_the_endpoints_are_untouched(self):
        """Same colour family. The ramp is re-parameterised along the
        same line, not replaced -- a theme's two ends are its identity,
        and that is as true of the dark ones as the light."""
        palettes = _palettes()
        pairs = palettes["light"] + palettes["dark"]
        ramps = _ramps(11)
        assert len(pairs) == len(ramps)
        for (low, high), ramp in zip(pairs, ramps, strict=True):
            assert ramp[0] == low
            assert ramp[-1] == high

    def test_dark_ramps_are_covered_at_all(self):
        """A guard on the guard: if the palettes stopped reaching these
        tests, every assertion above would pass over them without saying
        so."""
        light, dark = self._by_mode(11)
        assert len(dark) == len(light) >= 4


class TestTheNameThatBrokeACopyOfThis:
    def test_the_maximum_is_not_called_top(self):
        """`top` is a global in a browser. It survives here only because
        it sits inside a function; a flat copy of this block threw
        "Identifier 'top' has already been declared" and took the whole
        library down."""
        assert "const top = " not in CHART.read_text()
