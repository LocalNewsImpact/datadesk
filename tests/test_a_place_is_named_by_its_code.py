"""One town arrived as two dots because the model named a venue.

`article_enrichment.point_place` is the enrichment model's own words for
where a story is centred. `point_geoid` beside it is what the FIPS ladder
resolved. When the model answers with a VENUE rather than a locality, the
code is still right and the string is not.

FOUND IN PRODUCTION, 2026-09-15, across 832 coded places:

    2932572  Holden (11 articles)  /  "high school football field/track" (1)
    2959096  Poplar Bluff (49)     /  "Three Rivers College" (1)
    2951644  Nevada (64)           /  "Ella Maxwell Fine Arts Center" (1)
    2919792  Doniphan (38)         /  "Pilgrim's Rest Church" (1),
                                      "near Highway C" (1)
    2945848  Marble Hill (13)      /  "Woodland High School" (1)
    2932662  Hollister (10)        /  "Table Rock Career Center" (1)
    2910468  California (30)       /  "Oak Street Collectibles" (1)

Eight rows. The wrong label is the visible part; the UNDERCOUNT is the
harm. The points layer grouped by the string, so Holden read as 11 stories
when it had 12, and its football field appeared beside it as a place in
its own right with one story and one publisher.

In every one of those cases the coordinates were IDENTICAL -- the
geocoder had resolved to the Census place centroid, not to the venue --
so the two rows differed on nothing but the model's phrasing.

Two guards. The label comes from the code, and the code is what the rows
are grouped by; the model's string survives only as a fallback name for a
point that has no code to be named by.
"""

import pytest

from datasets.places import place_label

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


class TestAPlaceLabelComesFromTheGazetteer:
    """The bundled Census file is the authority. It is the crawler's own
    vendored copy, so both systems agree on what a place is."""

    @pytest.mark.parametrize(
        "geoid,expected",
        [
            ("2932572", "Holden, MO"),
            ("2959096", "Poplar Bluff, MO"),
            ("2951644", "Nevada, MO"),
            ("2919792", "Doniphan, MO"),
            ("2945848", "Marble Hill, MO"),
            ("2932662", "Hollister, MO"),
            ("2910468", "California, MO"),
        ],
    )
    def test_each_production_case_resolves_to_its_town(self, geoid, expected):
        assert place_label(geoid) == expected

    def test_the_state_rides_along(self):
        """Place names repeat across state lines -- there is a Nevada in
        Missouri and a Nevada that is a state -- so a bare name is
        ambiguous on a chart."""
        assert place_label("2951644").endswith(", MO")

    def test_the_lsad_suffix_is_stripped(self):
        """The gazetteer says "Holden city"; a reader wants "Holden"."""
        assert place_label("2932572") == "Holden, MO"

    @pytest.mark.parametrize("geoid", ["29101", "nonsense", "", None])
    def test_what_is_not_a_place_geoid_gets_no_place_label(self, geoid):
        """A county code is not a place code. Returning something for it
        would put a county's name on a place row -- `county_label` is what
        names those."""
        assert place_label(geoid) is None


class TestThePointsLayerIsGroupedByTheCode:
    """THE REGRESSION. `place` as a grouping key splits one town into one
    row per phrasing, and splits its counts with it."""

    def _points_source(self):
        from pathlib import Path

        source = Path("visuals/corpus.py").read_text()
        start = source.index("    points = list(")
        return source[start : start + 1400]

    def test_the_model_string_is_not_a_grouping_key(self):
        block = self._points_source()
        assert 'place=F("enrichment__point_place")' not in block, (
            "point_place is a grouping key again: one town will arrive as "
            "one row per phrasing"
        )

    def test_it_is_an_aggregate_instead(self):
        """Kept as a fallback, so a point with no code still has a name."""
        assert 'place=Min("enrichment__point_place")' in self._points_source()

    def test_the_code_and_the_coordinates_are_the_keys(self):
        block = self._points_source()
        for key in ("geoid=F", "level=F", "lat=F", "lon=F"):
            assert key in block

    def test_the_counts_are_still_distinct(self):
        """Merging rows must not turn a distinct publisher count into a
        sum -- the same publisher wrote both the Holden stories and the
        football-field one."""
        block = self._points_source()
        assert 'Count("id", distinct=True)' in block
        assert 'Count("candidate_link__source_id", distinct=True)' in block


class TestTheLabelIsAppliedToTheRows:
    def _relabel_block(self):
        from pathlib import Path

        source = Path("visuals/corpus.py").read_text()
        start = source.index("    for row in points:")
        return source[start : start + 700]

    def test_a_place_row_is_named_from_its_geoid(self):
        assert 'place_label(row["geoid"])' in self._relabel_block()

    def test_a_county_row_falls_back_to_the_county_name(self):
        assert 'county_label(row["geoid"])' in self._relabel_block()

    def test_a_row_with_no_resolvable_name_keeps_the_model_string(self):
        """A point placed by coordinates alone has no code to be named by.
        Blanking it would lose the only label it has."""
        block = self._relabel_block()
        assert "if named:" in block
