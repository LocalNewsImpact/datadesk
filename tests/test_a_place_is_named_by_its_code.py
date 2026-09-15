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
        """The relabel loop, bounded by what follows it rather than by a
        character count -- a fixed window silently stops covering the code
        as soon as somebody adds a comment."""
        from pathlib import Path

        source = Path("visuals/corpus.py").read_text()
        start = source.index("    for row in points:")
        end = source.index("A HUMAN CENTRE IS A DOT", start)
        return source[start:end]

    def test_a_named_level_is_labelled_from_its_geoid(self):
        block = self._relabel_block()
        assert 'level in ("place", "county", "state")' in block
        assert 'geoid_label(row["geoid"], level)' in block

    def test_an_unnamed_level_prefers_the_entity_it_was_given(self):
        """A block has no Census name, so the model's entity is the only
        one there is -- and it says something the digits cannot.

        The block-to-place crosswalk must NOT override it. "Ella Maxwell
        Fine Arts Center" tells a reader where in Nevada the story
        happened; "Nevada, MO" throws away the only thing the finer coding
        bought. The city fills a gap and drives aggregation; it does not
        rename anything."""
        block = self._relabel_block()
        entity = block.index('row.get("place")')
        crosswalk = block.index('geoid_label(city, "place")')
        assert entity < crosswalk, "the crosswalk is overwriting the entity name"

    def test_the_aggregation_key_is_computed_beside_the_label(self):
        """A map of cities groups by `city_geoid`, which is a different
        question from what the dot is called."""
        block = self._relabel_block()
        assert 'row["city_geoid"] = city_geoid(' in block
        assert "blocks=block_cities" in block

    def test_a_row_with_no_resolvable_name_keeps_the_model_string(self):
        """A point placed by coordinates alone has no code to be named by.
        Blanking it would lose the only label it has -- and at block level
        that string is the ONLY name there is."""
        block = self._relabel_block()
        assert "if named:" in block
        assert 'row["place"] = named' in block


class TestWhichLevelsTheCensusCanName:
    """Names stop at place.

        state   name         Missouri
        county  name         Johnson County
        place   name         Holden city
        tract   number only  Census Tract 21.03
        block   number only  043

    A block GEOID is digits all the way down: 290190021003043 is state 29,
    county 019, tract 0021.00, block group 3, block 043. So naming from
    the code is right down to place and impossible below it -- which is
    exactly why an entity name is worth keeping at block level and worth
    overriding at place level.
    """

    def test_a_state_code_resolves_to_a_state(self):
        """2,090 state-coded points carried no name at all: the enrichment
        records a state coding without a place string, because there is no
        place to name."""
        from datasets.geo import state_label

        assert state_label("29") == "Missouri"
        assert state_label("17") == "Illinois"

    def test_a_leading_zero_state_still_resolves(self):
        """FIPS are zero-padded; "06" is California and 6 is nothing."""
        from datasets.geo import state_label

        assert state_label("06") == "California"

    @pytest.mark.parametrize("geoid", ["2932572", "", None, "nope"])
    def test_what_is_not_a_state_code_gets_no_state_name(self, geoid):
        from datasets.geo import state_label

        assert state_label(geoid) is None

    def test_a_block_has_no_name_to_look_up(self):
        """Neither gazetteer can name one. This is the case the entity
        string exists to fill."""
        from datasets.geo import county_label
        from datasets.places import place_label

        assert place_label("290190021003043") is None
        # county_label echoes the code back when it cannot name it, which
        # is how the caller knows to fall back rather than print it.
        assert county_label("290190021003043") == "290190021003043"

    def test_a_block_is_placed_by_its_county_when_nothing_named_it(self):
        """Nine such rows. The first five digits of a block geoid ARE its
        county, so a dot that would otherwise render blank can at least be
        placed."""
        from datasets.geo import county_label

        assert county_label("290190021003043"[:5]) == "Boone, MO"
        assert county_label("291833119081051"[:5]) == "St. Charles, MO"


class TestAnEntityNameSurvivesWhereItCarriesPrecision:
    """The point of the distinction. At PLACE level a venue is standing in
    for a town that has a name of its own, so the code wins. At BLOCK
    level the venue says something the geoid cannot, and there is no
    Census name to prefer over it -- so it is kept."""

    def _relabel(self, geoid, level, model_string):
        from datasets.geo import county_label, state_label
        from datasets.places import place_label

        named = None
        if level == "place":
            named = place_label(geoid)
        elif level == "county":
            named = county_label(geoid)
        elif level == "state":
            named = state_label(geoid)
        if not named and not model_string and geoid:
            named = county_label(geoid[:5])
        return named or model_string

    def test_a_venue_at_place_level_is_replaced_by_its_town(self):
        assert (
            self._relabel("2932572", "place", "high school football field/track")
            == "Holden, MO"
        )

    def test_a_venue_at_block_level_is_kept(self):
        assert (
            self._relabel("290190021003043", "block", "Ella Maxwell Fine Arts Center")
            == "Ella Maxwell Fine Arts Center"
        )

    def test_an_unnamed_block_is_placed_not_blanked(self):
        assert self._relabel("290190021003043", "block", "") == "Boone, MO"

    def test_a_state_row_gains_a_name_it_never_had(self):
        assert self._relabel("29", "state", "") == "Missouri"


class TestTheNameLadderWalksUpToTheFirstNamedParent:
    """A code that cannot be named at its own level takes the name of the
    nearest parent that can be.

    The reachable ladder is narrower than it looks. Tract and block group
    have no names at all, and place -- though it IS a named level, and the
    first one tried for a 7-digit code -- can only be a starting point,
    never a destination: blocks nest in tracts and tracts in counties,
    while a city is a separate geography a block's digits never encode.

    So the walk is: own level if that level has names, then county, then
    state. Nothing arrives at a place that did not start there.
    """

    def test_a_block_takes_its_county(self):
        from datasets.geo import geoid_label

        assert geoid_label("290190021003043", "block") == "Boone, MO"
        assert geoid_label("291833119081051", "block") == "St. Charles, MO"

    def test_a_tract_takes_its_county_too(self):
        from datasets.geo import geoid_label

        assert geoid_label("29019002100", "tract") == "Boone, MO"

    def test_a_named_level_does_not_walk(self):
        """Holden must not come back as Johnson County just because a
        county is reachable from its code."""
        from datasets.geo import geoid_label

        assert geoid_label("2932572", "place") == "Holden, MO"
        assert geoid_label("29101", "county") == "Johnson, MO"
        assert geoid_label("29", "state") == "Missouri"

    def test_a_block_inside_a_city_still_comes_back_as_its_county(self):
        """THE DISTINCTION, AT ITS SHARPEST. Block 290190011064015 is in
        Columbia -- the enrichment that coded it said so, and Columbia has
        a place code of its own (2915670). The label is still "Boone, MO",
        because the block's digits do not contain Columbia and nothing
        here invents the connection.

        If this ever returns "Columbia, MO", something has started
        guessing a city from a code that does not carry one."""
        from datasets.geo import geoid_label

        assert geoid_label("290190011064015", "block") == "Boone, MO"

    def test_a_place_code_is_tried_before_walking(self):
        """Place IS a named level and the first one tried for a 7-digit
        code -- it is only unreachable from below, not unused."""
        from datasets.geo import geoid_label

        assert geoid_label("2915670", "place") == "Columbia, MO"
        assert geoid_label("2915670") == "Columbia, MO"

    def test_a_code_from_no_real_geography_names_nothing(self):
        """Better to have no label than a confident wrong one."""
        from datasets.geo import geoid_label

        assert geoid_label("999999999999999", "block") is None
        assert geoid_label("", "place") is None
        assert geoid_label(None, "place") is None
