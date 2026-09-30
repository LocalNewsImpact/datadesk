"""The shading was banded on counties the map does not draw.

A story map's feed carries every county the corpus touched. The Missouri
map's payload holds 710 of them; the map paints 115. The 595 outside the
frame have a median of TWO stories, and the cuts were computed over all
of them -- so the deciles came out at 1, 2, 4, 7, 19, 43 and every
Missouri county (median 44, maximum 969) landed above nearly all of
them.

MEASURED ON THE LIVE PAYLOAD for `mizzou-story-geography` v7: 95 of the
115 painted counties fell in the top two bands, four of the ten shades
were never drawn at all, and the state read as one flat colour. That is
the compression -- not the point layer, which the shading never touched.

Banded on the counties actually painted, the same data gives cuts of 15,
22, 30, 38, 44, 55, 70, 92, 170 and spreads the counties evenly across
all ten bands, which is what equal-count bands are for.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.chart_runtime import value

CHART = Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"

#: A payload shaped like the real one: counties in frame with real counts,
#: and a long tail out of frame with one or two stories each. Without the
#: tail this bug is invisible.
#:
#: THIRTY IN FRAME, not ten. Under `steps * 2` painted counties the
#: renderer does not take quantiles at all -- it falls back to the fixed
#: 2 / 5 / 9 cuts -- so a ten-county fixture never reaches the code the
#: Missouri map (115 counties) runs. The copy of the cut arithmetic this
#: file used to carry left that fallback out, and passed.
IN_FRAME = [
    15, 16, 19, 22, 24, 27, 30, 33, 36, 38,
    40, 42, 44, 47, 51, 55, 60, 65, 70, 77,
    84, 92, 105, 120, 140, 170, 210, 290, 480, 969,
]  # fmt: skip
OUT_OF_FRAME = [1, 2] * 60


def _areas():
    """The feed: in-frame counties 29xxx, the tail 20xxx."""
    inside = [{"geoid": f"29{i:03d}", "n": n} for i, n in enumerate(IN_FRAME)]
    outside = [{"geoid": f"20{i:03d}", "n": n} for i, n in enumerate(OUT_OF_FRAME)]
    return inside + outside


def _painted_bands(painted_prefix, config=None):
    """Band the feed as the renderer does, painting only `painted_prefix`.

    Both steps are the renderer's own -- `paintedValues` then
    `storyMapBands` -- rather than a copy of their arithmetic, which would
    only prove the copy right.
    """
    return value(f"""(() => {{
          const areas = {json.dumps(_areas())};
          const prefix = {json.dumps(painted_prefix)};
          const painted = new Set(
            areas.map((a) => a.geoid).filter((g) => g.startsWith(prefix)));
          const {{ max, values }} =
            T.paintedValues(areas, painted, (a) => a.n, null);
          const config = {json.dumps(config or {})};
          const b = T.storyMapBands(values, config, null, T.theme());
          const inFrame = {json.dumps(IN_FRAME)};
          return {{ max, values, cuts: b.cuts, bands: inFrame.map(b.bandOf) }};
        }})()""")


class TestTheOutOfFrameTailFlattensTheMap:
    def test_including_it_crowds_the_painted_counties(self):
        """THE BUG, as a number. With the tail in the sample nearly every
        county in frame lands in the top bands."""
        b = _painted_bands("")  # every county painted: the old behaviour
        top_two = sum(1 for band in b["bands"] if band >= len(b["cuts"]))
        assert top_two >= len(IN_FRAME) * 0.6, b["bands"]

    def test_excluding_it_uses_the_whole_ramp(self):
        """The fix, as the same number. Ten bands, and the counties
        spread across them."""
        b = _painted_bands("29")
        assert len(set(b["bands"])) >= 8, sorted(b["bands"])

    def test_the_tail_drags_the_cuts_down(self):
        """1, 2, 4, 7, 19, 43 against 15, 22, 30, ... -- the same data,
        banded over two different populations."""
        with_tail = _painted_bands("")["cuts"]
        without = _painted_bands("29")["cuts"]
        assert with_tail[0] < without[0]
        assert len(without) > len(with_tail), (with_tail, without)


class TestTheRendererBandsOnWhatItPaints:
    def test_only_painted_counties_are_banded(self):
        b = _painted_bands("29")
        assert b["values"] == sorted(IN_FRAME)

    def test_the_scale_gate_reads_the_painted_counties_too(self):
        """A frame with no stories in it must not draw a key for
        somebody else's counties."""
        assert _painted_bands("29")["max"] == 969
        assert _painted_bands("17")["max"] == 0

    def test_it_is_declared_before_it_is_read(self):
        """`const` is not hoisted. Declared after its first reader -- which
        is where it first went -- the whole renderer throws a
        ReferenceError and the map does not draw at all.

        The one ordering this file still reads from source: it is a
        property of `renderStoryMap`'s body, which draws into a DOM that
        the node harness does not have.
        """
        source = CHART.read_text()
        declared = source.index("const painted = new Set(")
        assert source.index("= paintedValues(areas, painted,") > declared

    def test_it_follows_the_frame_rather_than_the_focus(self):
        """`shown`, not `focus`: the frame can come from an explicit
        `config.frame`, a focus, or the auto weighting, and the bands
        have to follow whichever produced the map."""
        source = CHART.read_text()
        line = [ln for ln in source.splitlines() if "const painted = new Set(" in ln][0]
        assert "shown" in line
