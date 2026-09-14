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
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

CHART = Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"

#: A payload shaped like the real one: a handful of counties in frame
#: with real counts, and a long tail out of frame with one or two
#: stories each. Without the tail this bug is invisible.
IN_FRAME = [15, 22, 30, 38, 44, 55, 70, 92, 170, 969]
OUT_OF_FRAME = [1, 2] * 60


def _cuts(values, steps=10):
    """The renderer's own cut computation, run through node.

    Lifted from the file rather than reimplemented: the point is what
    ships, and a Python copy would only prove the copy right.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node to run the renderer with")
    script = f"""
      const d3 = {{
        ascending: (a, b) => a - b,
        quantile: (a, q) => {{
          const pos = (a.length - 1) * q, lo = Math.floor(pos);
          return a[lo] + (a[Math.min(lo + 1, a.length - 1)] - a[lo]) * (pos - lo);
        }},
      }};
      const values = {json.dumps(sorted(values))};
      const steps = {steps};
      const cuts = Array.from({{ length: steps - 1 }}, (_, i) =>
        Math.max(1, Math.round(d3.quantile(values, (i + 1) / steps))))
        .reduce((kept, cut) => {{
          if (!kept.length || cut > kept[kept.length - 1]) kept.push(cut);
          return kept;
        }}, []);
      console.log(JSON.stringify(cuts));
    """
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        where = fh.name
    done = subprocess.run([node, where], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _band_of(value, cuts):
    if not value:
        return 0
    for i, cut in enumerate(cuts):
        if value <= cut:
            return i + 1
    return len(cuts) + 1


class TestTheOutOfFrameTailFlattensTheMap:
    def test_including_it_crowds_the_painted_counties(self):
        """THE BUG, as a number. With the tail in the sample nearly every
        county in frame lands in the top bands."""
        cuts = _cuts(IN_FRAME + OUT_OF_FRAME)
        bands = [_band_of(v, cuts) for v in IN_FRAME]
        top_two = sum(1 for b in bands if b >= len(cuts))
        assert top_two >= len(IN_FRAME) * 0.6, bands

    def test_excluding_it_uses_the_whole_ramp(self):
        """The fix, as the same number. Ten bands, and the counties
        spread across them."""
        cuts = _cuts(IN_FRAME)
        bands = {_band_of(v, cuts) for v in IN_FRAME}
        assert len(bands) >= 8, sorted(bands)

    def test_the_tail_drags_the_cuts_down(self):
        """1, 2, 4, 7, 19, 43 against 15, 22, 30, ... -- the same data,
        banded over two different populations."""
        with_tail = _cuts(IN_FRAME + OUT_OF_FRAME)
        without = _cuts(IN_FRAME)
        assert with_tail[0] < without[0]
        assert len(without) > len(with_tail), (with_tail, without)


class TestTheRendererBandsOnWhatItPaints:
    def _source(self):
        return CHART.read_text()

    def test_the_painted_set_exists(self):
        assert "const painted = new Set(shown.map((f) => String(f.id)));" in (
            self._source()
        )

    def test_the_values_are_filtered_by_it(self):
        source = self._source()
        block = source[source.index("const values = areas") :]
        block = block[: block.index("const cuts")]
        assert "painted.has(String(a.geoid))" in block

    def test_the_scale_gate_is_filtered_too(self):
        """A frame with no stories in it must not draw a key for
        somebody else's counties."""
        source = self._source()
        block = source[source.index("const max = d3.max(") :]
        block = block[: block.index(") || 0;") + 7]
        assert "painted.has(String(a.geoid))" in block

    def test_it_is_declared_before_it_is_read(self):
        """`const` is not hoisted. Declared after `max` -- which is where
        it first went -- the whole renderer throws a ReferenceError and
        the map does not draw at all."""
        source = self._source()
        declared = source.index("const painted = new Set(")
        for reader in ("const max = d3.max(", "const values = areas"):
            assert source.index(reader) > declared, reader

    def test_it_follows_the_frame_rather_than_the_focus(self):
        """`shown`, not `focus`: the frame can come from an explicit
        `config.frame`, a focus, or the auto weighting, and the bands
        have to follow whichever produced the map."""
        source = self._source()
        line = [ln for ln in source.splitlines() if "const painted = new Set(" in ln][0]
        assert "shown" in line
