"""Flow arrows piled up in the counties that had the most flow.

Four defects, one symptom. Every arrival into Boone overlapped another,
and the placement search -- which exists to prevent exactly that -- had
searched 490 arrangements per pair and reported the pile as its best
work.

THE SCALE BUG, which hid the other three. `clash` scored an overlap as
`(need - gap) ** 2`, and `need` comes from the arrows' widths. That is a
cost in units of width SQUARED, so the fattest arrows scored their
overlaps in the thousands while every other term in the cost function --
arc, length, border spacing, trespass -- was worth tens. Measured on the
Columbia-Jefferson City payload: Cooper->Boone's best arrangement cost
9,899, of which 9,899 was clash. The other terms were computed correctly
and then drowned. The search was not ignoring length and arc; it could
not hear them.

THE FLOOR THAT WAS MEANT TO BE A TARGET. `want` -- a head plus a little
shaft -- is the shortest arrow that still reads as one. It was used as
the FLOOR under a fixed `DEEP = 0.68`, so every arrow drove 68% of the
way to the destination's centroid whatever its width. It is now the
target and the old depth is the cap.

THE SAME CONSTANT IN FOUR PLACES. `wide * 2.5` was hardcoded at the
landing, the collision trace, the legality check and the drawn path, so
the minimum length of any arrow was 4.9 times its own width. For the
widest arrows that floor, not the geography, set the length: Callaway's
arrow into Boone ran most of the way across the county because it was
fat, not because it had far to go.

THE FAN SPREAD THE WRONG END. Landing points were fanned around the
destination's centroid, so two arrivals could stop well apart having
entered the county through the same few pixels of border -- and lain on
each other the whole way in. Osage read clearly because its arrivals
happened to come in through different stretches of its border; costing
the distance between border crossings is what makes that general rather
than lucky.

Measured on the same payload after: total residual overlap 2,142 -> 209,
pairs with real overlap 8/17 -> 1/17, and the tightest arc used across
the whole map fell from the maximum bend to the flattest -- no arrow is
hooked around anything any more.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

CHART = Path(__file__).resolve().parent.parent / "static/js/datadesk-chart.js"
JS = CHART.read_text()


def _run(script):
    """Run a snippet in node and read back its JSON.

    Written to a file rather than passed with `-e`: Linux caps a single
    argument at 131,072 bytes and the lifted source runs past it.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node to run the renderer with")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        where = fh.name
    done = subprocess.run([node, where], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _lift(pattern):
    """A helper's source, taken from the file that ships.

    Reimplementing it in the test would only prove the reimplementation
    right.
    """
    found = re.search(pattern, JS, re.S)
    assert found, f"{pattern} is gone from the renderer"
    return found.group(0)


# --- the scale bug -----------------------------------------------------------


class TestOverlapCostsTheSameWhateverTheWidth:
    """THE ONE THAT HID THE OTHER THREE."""

    def _cost(self, wide):
        """What a head-on overlap of two arrows of this width costs."""
        body = _lift(
            r"const need = \(p\.wide \+ q\.wide\) / 2 \+ 3;"
            r".*?\n            if \(gap < need\) cost \+= [^;]+;"
        )
        return _run(f"""
            let cost = 0;
            const p = {{ x: 0, y: 0, wide: {wide} }};
            const q = {{ x: 0, y: 0, wide: {wide} }};
            {body}
            console.log(JSON.stringify(Math.round(cost)));
            """)

    def test_a_thin_and_a_fat_overlap_score_alike(self):
        """THE REGRESSION. Unnormalised, a 20px arrow's overlap outscored a
        3px arrow's by more than thirty to one, so the search spent its
        whole budget on the fat arrows and ignored everything else."""
        thin, fat = self._cost(3), self._cost(20)
        assert abs(thin - fat) <= 5, f"width still drives the cost: {thin} vs {fat}"

    def test_no_single_point_can_dominate_the_cost(self):
        """The cap is the whole point. Unnormalised, one point-pair of the
        widest arrows was worth 144 and a leg's worth ran into the
        thousands -- more than the entire rest of the cost function put
        together. Capped, a leg (fifteen sampled points) lying flat on
        another is commensurate with a crossing at 400."""
        one = self._cost(8)
        assert one <= 100, f"one point-pair is worth {one}"
        assert 300 <= one * 15 <= 1500

    def test_the_raw_squared_form_is_gone(self):
        """`(need - gap) ** 2` is the shape to keep out: it reads naturally
        and it is what made every other term unhearable."""
        assert "cost += (need - gap) ** 2;" not in JS


# --- as short as practical to be clearly directional -------------------------


class TestAWideArrowNeedsLessShaftThanAThinOne:
    def _lean(self, wide, fat=20):
        body = _lift(r"const leanOf = \(wide\) => Math\.max\(\s*[^;]*;")
        return _run(f"""
            const fat = {fat};
            {body}
            console.log(JSON.stringify(leanOf({wide})));
            """)

    def test_the_widest_arrow_gets_the_shortest_shaft(self):
        """A wide arrow's head is already unmistakable; a hairline one needs
        length to read at all."""
        assert self._lean(20) < self._lean(3)

    def test_a_stub_is_still_long_enough_to_read(self):
        """Shortening has a floor. An arrowhead with no shaft is a triangle,
        not an arrow."""
        assert self._lean(20) >= 0.8
        assert self._lean(1000) >= 0.8

    def test_it_never_exceeds_the_old_constant(self):
        """2.5 was the flat value; nothing should now come out longer."""
        assert self._lean(1) <= 2.6


class TestOneLengthRuleNotFour:
    """`wide * 2.5` was hardcoded at the landing, the collision trace, the
    legality check and the drawn path. Four copies of one rule is how the
    drawn arrow came to disagree with the arrow the search had costed."""

    def test_no_site_still_hardcodes_the_constant(self):
        assert "wide * 2.5" not in JS, "a hardcoded shaft length is back"

    def test_every_measuring_site_uses_the_shared_rule(self):
        """The trace and the legality check measure twice; the landing and
        the drawn path once each."""
        assert JS.count("leanOf(wide)") >= 4

    def test_the_drawn_path_measures_the_same_way_as_the_search(self):
        """The defect this guards: a route drawn longer than the route the
        collision search approved overlaps things the search cleared."""
        drawn = _lift(r"const run = Math\.max\(\(c\.reach\.get\(r\.b\)[^;]*;")
        traced = _lift(r"const far = Math\.max\(\(c\.reach\.get\(leg\.b\)[^;]*;")
        assert "leanOf(wide)" in drawn
        assert "leanOf(wide)" in traced


class TestTheLegibleLengthIsTheTargetNotTheFloor:
    def test_the_depth_is_capped_not_floored(self):
        """THE INVERSION. `Math.max(DEEP * back, want / reach)` drove every
        arrow at least 68% of the way to the centroid; the rule is that an
        arrow points at the centre and stops as soon as it reads."""
        depth = _lift(r"const depth = Math\.min\([^;]*;")
        assert "Math.max(\n          (r.rank === 0 ? DEEP : SHALLOW)" not in JS
        assert "cap" in depth

    def test_the_cap_shrinks_as_the_arrow_fattens(self):
        """For the widest arrows `want` runs past the centroid, so the cap is
        what binds -- and a flat cap put the fattest arrow, the one that
        least needs the distance, furthest into the county."""
        cap = _lift(r"const cap = \(r\.rank === 0 \? DEEP : SHALLOW\)[^;]*;")
        assert "rel" in cap


# --- arranged evenly around the border ---------------------------------------


class TestArrivalsSpreadAlongTheBorderTheyCrossed:
    def _bunched(self, gap, want=60):
        body = _lift(r"const bunched = \(doors, already\) => \{.*?\n      \};")
        return _run(f"""
            const apart = new Map([["b", {want}]]);
            {body}
            console.log(JSON.stringify(Math.round(bunched(
              [{{ geoid: "b", x: 0, y: 0 }}],
              [{{ geoid: "b", x: {gap}, y: 0 }}]))));
            """)

    def test_two_arrivals_through_the_same_point_cost_a_crossing(self):
        """Entering a county through the same few pixels is as bad as
        crossing something, and has to be priced like it."""
        assert 350 <= self._bunched(0) <= 450

    def test_far_enough_apart_is_free(self):
        """Semi-equally distributed, not equally: past the target it stops
        pushing, so the exterior border and the arrows already placed can
        still decide."""
        assert self._bunched(60) == 0
        assert self._bunched(200) == 0

    def test_it_falls_off_with_distance(self):
        assert self._bunched(10) > self._bunched(30) > self._bunched(50)

    def test_a_different_county_is_not_crowded(self):
        """Two arrows entering two different counties at the same screen
        position are not competing for the same border."""
        body = _lift(r"const bunched = \(doors, already\) => \{.*?\n      \};")
        assert _run(f"""
                const apart = new Map([["b", 60], ["c", 60]]);
                {body}
                console.log(JSON.stringify(bunched(
                  [{{ geoid: "b", x: 0, y: 0 }}],
                  [{{ geoid: "c", x: 0, y: 0 }}])));
                """) == 0

    def test_the_search_actually_pays_it(self):
        """A cost nothing consults is a comment."""
        cost = _lift(r"const cost = clash\(trace, placed, laid\)[^;]*;")
        assert "bunched(doors, doorway)" in cost

    def test_the_target_scales_with_the_county(self):
        """A big county has a long border to spread across and a small one
        does not, so the target cannot be a screen constant."""
        assert "path.bounds(shape)" in JS


# --- short and straight beats long and bent ----------------------------------


class TestBendingIsDearerThanShortening:
    def _weights(self):
        cost = _lift(r"\+ si \* \d+ \+ ci \* \d+ \+ ri \* \d+")
        return {k: int(v) for k, v in re.findall(r"(si|ci|ri) \* (\d+)", cost)}

    def test_curving_costs_more_than_shortening(self):
        """THE INVERSION THAT BENT THE MAP. Shortening was the dearest escape
        from a collision and bending among the cheapest, so the search
        bought its way out of every crowded county with the tightest arc on
        offer while barely shortening at all."""
        w = self._weights()
        assert w["ci"] > w["ri"], f"bending is still cheap: {w}"

    def test_shortening_is_the_cheapest_of_the_three(self):
        """An arrow that stops earlier still starts in the right county,
        still points the right way and still carries its width."""
        w = self._weights()
        assert w["ri"] <= w["si"] and w["ri"] <= w["ci"]

    def test_the_search_can_shorten_substantially(self):
        """34% was the most it could take off, which is not enough to clear
        a crowded county."""
        run = _lift(r"const RUN = \[[^\]]*\]")
        shortest = min(float(x) for x in re.findall(r"[\d.]+", run))
        assert shortest <= 0.55, f"RUN stops at {shortest}"


# --- the right colour coding -------------------------------------------------


class TestShadeSaysWhichWayTheFlowRuns:
    def _colour(self, a_subject, b_subject, rank=0):
        body = _lift(r"const colourOf = \(r\) => \{.*?\n      \};")
        pins = []
        if a_subject:
            pins.append('"a"')
        if b_subject:
            pins.append('"b"')
        return _run(f"""
            const pin = new Set([{", ".join(pins)}]);
            const isSubject = (g) => pin.has(g);
            const shadeBig = "DARK", shadeSmall = "LIGHT";
            {body}
            console.log(JSON.stringify(
              colourOf({{ a: "a", b: "b", rank: {rank} }})));
            """)

    def test_leaving_a_highlighted_county_is_dark(self):
        assert self._colour(True, False) == "DARK"

    def test_arriving_in_a_highlighted_county_is_light(self):
        assert self._colour(False, True) == "LIGHT"

    def test_between_two_highlighted_counties_the_bigger_flow_is_dark(self):
        """Both directions are on the same circle; the shade has to say which
        of the two carries more, since direction no longer can."""
        assert self._colour(True, True, rank=0) == "DARK"
        assert self._colour(True, True, rank=1) == "LIGHT"


class TestEveryChartMintsItsOwnArrowheads:
    """SVG resolves `marker-end` by id across the whole document. The id was
    `dd-ar-<n>-<hash of width>` and the counter reset on every render, so two
    flow maps of the same width on one page both minted the same ids and the
    second chart's arrows wore the first chart's colours."""

    def test_the_sequence_is_module_scope(self):
        assert re.search(
            r"^\s*let arrowSeq = 0;", JS, re.M
        ), "the counter is back inside the render function"

    def test_the_id_carries_it(self):
        assert re.search(r"`dd-ar-\$\{mint\}-\$\{arrowIds\.size\}`", JS)


# --- width is the commuter count ---------------------------------------------


class TestWidthIsTheHeadcountAndNothingElse:
    """A share of the subject county's own traffic INVERTS across counties
    of different sizes: 90% of a very small county's outflow draws wider
    than 50% of a very large one's. Measured on the Audrain/Boone/Osage
    map, 13% of arrow pairs had the wider arrow carrying fewer people --
    worst case 10.6x, `Miller -> Osage` at 173 people drawing wider than
    `Randolph -> Boone` at 1,841.

    The whole of the arrow geometry -- the shortening, the border spacing,
    the trimmed maximum -- was measured against the headcount. A switch
    that could put it back on shares meant the shipped default was drawing
    the one arrangement none of it was tuned for.
    """

    def _flow(self):
        return JS.split("function renderFlowMap(")[1].split("\n  function ")[0]

    def test_width_reads_the_headcount(self):
        assert "const widthOf = (r) => r.n;" in self._flow()

    def test_there_is_no_basis_switch(self):
        """THE REGRESSION. `config.width_basis || "own"` shipped defaulting
        to the inverting basis, so every published map drew shares while
        every measurement behind the geometry was taken on counts."""
        flow = self._flow()
        assert "width_basis" not in flow, "the width switch is back"
        assert "byPeople" not in flow

    def test_the_scale_is_a_square_root(self):
        """Counts are heavily skewed; on a linear scale the top flows sit at
        the cap and everything else reads as absent rather than smaller.
        The root compresses the top without reordering anything."""
        assert "const w = d3.scaleSqrt()" in self._flow()

    def test_share_still_means_a_share(self):
        """`share` keeps its own meaning and its own jobs -- the 3% floor,
        the fan-out order and the tooltip. Making `share` ITSELF a
        headcount, rather than separating width from it, broke the floor:
        every count is above 0.03, so nothing filtered and the map filled
        with arrows."""
        flow = self._flow()
        assert "r.share >= FLOOR" in flow
        assert "y.share - x.share" in flow
        assert "(r.share * 100).toFixed(1)" in flow
