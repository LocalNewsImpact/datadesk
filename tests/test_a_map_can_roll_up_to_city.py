"""One town, several dots.

The corpus codes a story's centre at four precisions -- place, block,
county, state. Drawn as coded, a story placed to a block sits at the
block and one placed to the city sits at the city, so a town can carry
several dots. That is the truest reading and the noisiest, and a map OF
CITIES wants them counted together.

`roll_up: "city"` merges every point that belongs to a city into that
city. What the merge must get right:

    STORIES ADD.       Each article has one point, so the groups are
                       disjoint and a sum is exact.
    PUBLISHERS DO NOT. Two dots in one town may share a publisher, and
                       adding two distinct-counts would report it twice.
                       The union of the ids is counted instead, which is
                       why the query aggregates ids and not only counts.
    COORDINATES MOVE.  The merged dot is the city, not any of the points
                       that went into it, so it takes the city's centre.
    COUNTY AND STATE   codings belong to no city. They are left exactly
                       where they were rather than invented into one.
"""

import pytest

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def _rows():
    """Two block-coded points in Columbia, one place-coded point in
    Columbia, and a county-coded point that belongs to no city.

    The two blocks share publisher s1, which is the case a summed
    publisher count gets wrong.
    """
    return [
        {
            "geoid": "290190011064015",
            "level": "block",
            "place": "Ella Center",
            "lat": 38.9,
            "lon": -92.3,
            "stories": 3,
            "publishers": 1,
            "city_geoid": "2915670",
            "city": "Columbia, MO",
        },
        {
            "geoid": "290190021003043",
            "level": "block",
            "place": None,
            "lat": 38.95,
            "lon": -92.33,
            "stories": 2,
            "publishers": 2,
            "city_geoid": "2915670",
            "city": "Columbia, MO",
        },
        {
            "geoid": "2915670",
            "level": "place",
            "place": "Columbia, MO",
            "lat": 38.95,
            "lon": -92.33,
            "stories": 10,
            "publishers": 2,
            "city_geoid": "2915670",
            "city": "Columbia, MO",
        },
        {
            "geoid": "29019",
            "level": "county",
            "place": "Boone, MO",
            "lat": 38.99,
            "lon": -92.31,
            "stories": 4,
            "publishers": 1,
            "city_geoid": None,
            "city": None,
        },
    ]


def _roll(rows, setting="city", publishers=None):
    """The shipped roll-up, lifted from the module that runs it.

    `base` is stubbed: the block fetches publisher ids for the handful of
    dots that actually merge, and the test says who wrote where instead of
    reaching a database.
    """
    import re
    from pathlib import Path

    from datasets.geo import centroid

    source = Path("visuals/corpus.py").read_text()
    start = source.index('    if (config or {}).get("roll_up")')
    end = source.index('    points.sort(key=lambda r: -r["stories"])')
    body = re.sub(r"^    ", "", source[start:end], flags=re.M)

    pairs = [
        (geoid, source_id)
        for geoid, ids in (publishers or {}).items()
        for source_id in ids
    ]

    class _Values:
        def __init__(self, rows):
            self._rows = rows

        def distinct(self):
            return self._rows

    class _Base:
        """Honours the filter it is given, so a test fails if the roll-up
        asks for the wrong geoids rather than quietly getting them all."""

        def __init__(self):
            self.wanted = None

        def filter(self, **kwargs):
            self.wanted = set(kwargs.get("enrichment__point_geoid__in") or [])
            return self

        def values_list(self, *fields):
            return _Values(
                [p for p in pairs if self.wanted is None or p[0] in self.wanted]
            )

    scope = {
        "config": {"roll_up": setting},
        "points": list(rows),
        "centroid": centroid,
        "MAX_GROUPS": 200,
        "base": _Base(),
    }
    exec(body, scope)  # noqa: S102 - the shipped code, not a copy
    return scope["points"]


#: Who wrote at each dot. s1 is at both blocks and s2 at a block and the
#: city, which is the overlap a summed publisher count gets wrong.
WHO_WROTE = {
    "290190011064015": ["s1"],
    "290190021003043": ["s1", "s2"],
    "2915670": ["s2", "s3"],
    "29019": ["s4"],
}


class TestRollingUpToCity:
    def test_the_two_blocks_and_the_place_become_one_dot(self):
        rolled = _roll(_rows(), publishers=WHO_WROTE)
        columbia = [r for r in rolled if r["geoid"] == "2915670"]
        assert len(columbia) == 1

    def test_stories_add_because_the_groups_are_disjoint(self):
        rolled = _roll(_rows(), publishers=WHO_WROTE)
        columbia = next(r for r in rolled if r["geoid"] == "2915670")
        assert columbia["stories"] == 15  # 3 + 2 + 10

    def test_publishers_are_a_union_not_a_sum(self):
        """THE ONE A SUM GETS WRONG. s1 wrote at both blocks and s2 at a
        block and the city: 1 + 2 + 2 = 5, but there are only three
        distinct publishers."""
        rolled = _roll(_rows(), publishers=WHO_WROTE)
        columbia = next(r for r in rolled if r["geoid"] == "2915670")
        assert columbia["publishers"] == 3

    def test_the_merged_dot_sits_at_the_city(self):
        """It is the city now, not any of the points that went into it."""
        from datasets.geo import centroid

        lat, lon = centroid("2915670")
        rolled = _roll(_rows(), publishers=WHO_WROTE)
        columbia = next(r for r in rolled if r["geoid"] == "2915670")
        assert (columbia["lat"], columbia["lon"]) == (lat, lon)

    def test_the_merged_dot_is_named_for_the_city(self):
        rolled = _roll(_rows(), publishers=WHO_WROTE)
        columbia = next(r for r in rolled if r["geoid"] == "2915670")
        assert columbia["place"] == "Columbia, MO"

    def test_a_county_coding_is_left_where_it_was(self):
        """It belongs to no city, and putting it in one would claim
        something the coding never said."""
        rolled = _roll(_rows(), publishers=WHO_WROTE)
        county = next(r for r in rolled if r["geoid"] == "29019")
        assert county["stories"] == 4
        assert county["level"] == "county"

    def test_nothing_is_lost(self):
        rolled = _roll(_rows(), publishers=WHO_WROTE)
        assert sum(r["stories"] for r in rolled) == 19  # 3+2+10+4


class TestShowingEveryPointWhereItWasCoded:
    def test_the_default_merges_nothing(self):
        rows = _rows()
        assert _roll(rows, setting="", publishers=WHO_WROTE) == rows

    def test_every_dot_survives(self):
        rolled = _roll(_rows(), setting="", publishers=WHO_WROTE)
        assert len(rolled) == 4


class TestTheOptionIsOffered:
    def test_a_story_map_declares_it(self):
        from visuals.types import options_for

        assert "roll_up" in [o.id for o in options_for("storymap", [])]

    def test_its_default_is_to_show_every_point(self):
        """The first value is what a visual does when nothing is set, and
        the honest default is the coding as it stands."""
        from visuals.types import options_for

        option = next(o for o in options_for("storymap", []) if o.id == "roll_up")
        assert option.values[0][0] == ""

    def test_the_builder_keeps_the_key(self):
        """A config key not on the whitelist is stripped on save, so the
        control would appear to work and forget."""
        from visuals.builder import _STRING_KEYS

        assert "roll_up" in _STRING_KEYS


class TestAManuallyPlacedPointIsTreatedLikeAnyOther:
    """A reviewer's own dot is appended to the point list AFTER the
    pipeline's, so anything applied to the list before that reaches only
    half of it.

    FOUND BY RUNNING THE REAL MAP, not by reading the code: seven
    manually placed rows came back with no state, no county and no city
    while every pipeline row had all three. A roll-up had the same shape
    of bug -- it would have left a reviewer's dot sitting beside the city
    it belongs to instead of inside it.

    The ladder fill and the roll-up now run after the merge, over the
    whole list.
    """

    def _source(self):
        from pathlib import Path

        return Path("visuals/corpus.py").read_text()

    def test_the_ladder_is_filled_after_the_human_centres(self):
        source = self._source()
        manual = source.index("A HUMAN CENTRE IS A DOT")
        fill = source.index('if "state_geoid" not in row:')
        assert manual < fill, "the ladder is filled before the manual points join"

    def test_the_roll_up_runs_after_them_too(self):
        source = self._source()
        manual = source.index("A HUMAN CENTRE IS A DOT")
        rollup = source.index("# ROLL UP TO CITY")
        assert manual < rollup, "a reviewer's dot would not be rolled up"

    def test_both_run_before_the_list_is_trimmed(self):
        """Trimming to MAX_GROUPS first would drop points that the
        roll-up was about to merge, changing the totals."""
        source = self._source()
        rollup = source.index("# ROLL UP TO CITY")
        trim = source.index("del points[MAX_GROUPS:]")
        assert rollup < trim

    def test_the_fill_does_not_redo_rows_that_have_it(self):
        """The pipeline's rows are laddered in their own loop. Doing it
        twice would be a second crosswalk lookup per row for no change."""
        assert 'if "state_geoid" not in row:' in self._source()
