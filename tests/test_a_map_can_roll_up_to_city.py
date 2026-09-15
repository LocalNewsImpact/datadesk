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
            "_publisher_ids": ["s1"],
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
            "_publisher_ids": ["s1", "s2"],
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
            "_publisher_ids": ["s2", "s3"],
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
            "_publisher_ids": ["s4"],
        },
    ]


def _roll(rows, setting="city"):
    """The shipped roll-up, lifted from the module that runs it."""
    import re
    from pathlib import Path

    source = Path("visuals/corpus.py").read_text()
    start = source.index('    if (config or {}).get("roll_up")')
    end = source.index("    # The ids were only ever for the roll-up.")
    body = re.sub(r"^    ", "", source[start:end], flags=re.M)
    from datasets.geo import centroid

    MAX_GROUPS = 200
    scope = {
        "config": {"roll_up": setting},
        "points": list(rows),
        "centroid": centroid,
        "MAX_GROUPS": MAX_GROUPS,
    }
    exec(body, scope)  # noqa: S102 - the shipped code, not a copy
    return scope["points"]


class TestRollingUpToCity:
    def test_the_two_blocks_and_the_place_become_one_dot(self):
        rolled = _roll(_rows())
        columbia = [r for r in rolled if r["geoid"] == "2915670"]
        assert len(columbia) == 1

    def test_stories_add_because_the_groups_are_disjoint(self):
        rolled = _roll(_rows())
        columbia = next(r for r in rolled if r["geoid"] == "2915670")
        assert columbia["stories"] == 15  # 3 + 2 + 10

    def test_publishers_are_a_union_not_a_sum(self):
        """THE ONE A SUM GETS WRONG. s1 wrote at both blocks and s2 at a
        block and the city: 1 + 2 + 2 = 5, but there are only three
        distinct publishers."""
        rolled = _roll(_rows())
        columbia = next(r for r in rolled if r["geoid"] == "2915670")
        assert columbia["publishers"] == 3

    def test_the_merged_dot_sits_at_the_city(self):
        """It is the city now, not any of the points that went into it."""
        from datasets.geo import centroid

        lat, lon = centroid("2915670")
        rolled = _roll(_rows())
        columbia = next(r for r in rolled if r["geoid"] == "2915670")
        assert (columbia["lat"], columbia["lon"]) == (lat, lon)

    def test_the_merged_dot_is_named_for_the_city(self):
        rolled = _roll(_rows())
        columbia = next(r for r in rolled if r["geoid"] == "2915670")
        assert columbia["place"] == "Columbia, MO"

    def test_a_county_coding_is_left_where_it_was(self):
        """It belongs to no city, and putting it in one would claim
        something the coding never said."""
        rolled = _roll(_rows())
        county = next(r for r in rolled if r["geoid"] == "29019")
        assert county["stories"] == 4
        assert county["level"] == "county"

    def test_nothing_is_lost(self):
        rolled = _roll(_rows())
        assert sum(r["stories"] for r in rolled) == 19  # 3+2+10+4


class TestShowingEveryPointWhereItWasCoded:
    def test_the_default_merges_nothing(self):
        rows = _rows()
        assert _roll(rows, setting="") == rows

    def test_every_dot_survives(self):
        rolled = _roll(_rows(), setting="")
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
