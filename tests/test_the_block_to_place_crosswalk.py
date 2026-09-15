"""A block knows its city; a block GEOID does not.

`290190011064015` decomposes to state 29, county 019, tract 0021.00,
block group 3, block 043. Nowhere in those digits is Columbia. A place is
a separate geography that cuts across tracts, so every other lookup in
`datasets.geo` -- which walks up a code by slicing it -- stops at county.

The Census publishes the assignment per state as a Block Assignment File:

    BlockAssign_ST29_MO_INCPLACE_CDP.txt     BLOCKID|PLACEFP
    290190011064015|15670                    -> 2915670 Columbia

MEASURED against the real Missouri file, 2020 vintage: 253,633 blocks
from a 4.3 MB download, 119,139 in a place and **134,493 -- 53% --
unincorporated and in none**. That majority is why "no city" has to be a
stored answer rather than a missing row: otherwise every unincorporated
block sends the next caller back to census.gov.

Three real blocks this corpus has coded stories to:

    290190011064015 -> Columbia, MO       (the model said Columbia too)
    290190021003043 -> Columbia, MO       (fell back to "Boone, MO")
    291833119081051 -> Lake St. Louis, MO (fell back to "St. Charles, MO")
"""

from io import BytesIO, StringIO
from zipfile import ZipFile

import pytest
from django.core.management import call_command

from datasets import blockplace
from datasets.models import BlockPlace, BlockPlaceLoad

pytestmark = pytest.mark.django_db


def _archive(rows, fips="29", usps="MO"):
    """A Block Assignment File shaped like the Census one."""
    body = "BLOCKID|PLACEFP\n" + "".join(f"{b}|{p}\n" for b, p in rows)
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(f"BlockAssign_ST{fips}_{usps}_INCPLACE_CDP.txt", body)
        # The real archive carries six other components -- school
        # districts, voting districts, legislative districts. Parsing must
        # pick its own out rather than taking the first file.
        archive.writestr(f"BlockAssign_ST{fips}_{usps}_SDELM.txt", "BLOCKID|SDELM\n")
        archive.writestr(f"BlockAssign_ST{fips}_{usps}_VTD.txt", "BLOCKID|VTD\n")
    return buffer.getvalue()


REAL = [
    ("290190011064015", "15670"),  # Columbia
    ("290190021003043", "15670"),  # Columbia
    ("291833119081051", "40043"),  # Lake St. Louis
    ("290019501001000", ""),  # unincorporated, as 53% of them are
]


class TestParsingTheCensusFile:
    def test_a_place_code_becomes_a_place_geoid(self):
        """The file gives five digits; a place GEOID is the state in
        front of them."""
        rows = dict(blockplace.parse(_archive(REAL), "29", "MO"))
        assert rows["290190011064015"] == "2915670"
        assert rows["291833119081051"] == "2940043"

    def test_an_unincorporated_block_is_kept_with_no_place(self):
        """THE MAJORITY CASE. Dropping these would make "no city"
        indistinguishable from "not fetched"."""
        rows = dict(blockplace.parse(_archive(REAL), "29", "MO"))
        assert rows["290019501001000"] == ""

    def test_the_right_component_is_read(self):
        """The archive holds seven files. Reading the wrong one would
        assign blocks to school districts."""
        rows = dict(blockplace.parse(_archive(REAL), "29", "MO"))
        assert len(rows) == len(REAL)


class TestLoadingAState:
    def _load(self, rows=REAL):
        return blockplace.load_state("29", "MO", fetch=lambda url: _archive(rows))

    def test_it_stores_every_block_including_the_placeless(self):
        load = self._load()
        assert BlockPlace.objects.count() == 4
        assert load.blocks == 4
        assert load.in_a_place == 3

    def test_it_records_that_the_state_was_loaded(self):
        self._load()
        assert blockplace.is_loaded("29")
        assert not blockplace.is_loaded("50")

    def test_loading_twice_does_not_refetch(self):
        """Safe to call from a path that cannot know whether it is the
        first. A second download of a quarter-million rows on every map
        view would be worse than the problem."""
        self._load()
        calls = []

        def counting_fetch(url):
            calls.append(url)
            return _archive(REAL)

        blockplace.load_state("29", "MO", fetch=counting_fetch)
        assert calls == []
        assert BlockPlace.objects.count() == 4

    def test_the_url_names_the_state_and_vintage(self):
        url = blockplace.url_for("29", "MO")
        assert "BlockAssign_ST29_MO.zip" in url
        assert f"baf{blockplace.VINTAGE}" in url


class TestLookingUpACity:
    @pytest.fixture(autouse=True)
    def loaded(self):
        blockplace.load_state("29", "MO", fetch=lambda url: _archive(REAL))

    def test_a_block_resolves_to_its_place(self):
        assert blockplace.place_for_block("290190011064015") == "2915670"

    def test_an_unincorporated_block_resolves_to_nothing(self):
        assert blockplace.place_for_block("290019501001000") is None

    def test_an_unknown_block_resolves_to_nothing(self):
        assert blockplace.place_for_block("290190011069999") is None

    @pytest.mark.parametrize("bad", ["", None, "2932572", "29101", "nonsense"])
    def test_what_is_not_a_block_is_not_looked_up(self, bad):
        assert blockplace.place_for_block(bad) is None

    def test_the_bulk_lookup_is_one_query(self, django_assert_num_queries):
        """A map relabels every row. One query per row is the N+1 that
        makes a page slow for the sake of a handful of points."""
        with django_assert_num_queries(1):
            found = blockplace.places_for_blocks(
                ["290190011064015", "290190021003043", "291833119081051"]
            )
        assert len(found) == 3

    def test_the_bulk_lookup_omits_the_placeless(self):
        found = blockplace.places_for_blocks(["290190011064015", "290019501001000"])
        assert set(found) == {"290190011064015"}

    def test_no_blocks_means_no_query(self, django_assert_num_queries):
        with django_assert_num_queries(0):
            assert blockplace.places_for_blocks([]) == {}


class TestTheAggregationKey:
    """What a map of cities groups by."""

    @pytest.fixture(autouse=True)
    def loaded(self):
        blockplace.load_state("29", "MO", fetch=lambda url: _archive(REAL))

    def test_a_place_is_already_its_own_city(self):
        assert blockplace.city_geoid("2932572", "place") == "2932572"

    def test_a_block_becomes_the_city_it_sits_in(self):
        assert blockplace.city_geoid("290190011064015", "block") == "2915670"

    @pytest.mark.parametrize("geoid,level", [("29101", "county"), ("29", "state")])
    def test_a_coarser_coding_belongs_to_no_city(self, geoid, level):
        """There is no honest way down. Guessing would put stories in a
        city they were never coded to."""
        assert blockplace.city_geoid(geoid, level) is None

    def test_an_unincorporated_block_belongs_to_no_city(self):
        assert blockplace.city_geoid("290019501001000", "block") is None

    def test_it_uses_a_prepared_lookup_when_given_one(self, django_assert_num_queries):
        blocks = blockplace.places_for_blocks(["290190011064015"])
        with django_assert_num_queries(0):
            assert (
                blockplace.city_geoid("290190011064015", "block", blocks=blocks)
                == "2915670"
            )


class TestTheCommand:
    def _run(self, *args, **kwargs):
        out = StringIO()
        call_command("load_block_places", *args, stdout=out, **kwargs)
        return out.getvalue()

    def test_it_reports_what_is_stored(self):
        blockplace.load_state("29", "MO", fetch=lambda url: _archive(REAL))
        assert "MO" in self._run("--all-loaded")

    def test_it_says_so_when_nothing_is_stored(self):
        """Empty and broken must not look alike."""
        assert "No state crosswalks" in self._run("--all-loaded")

    def test_an_unknown_state_is_refused(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            self._run("Freedonia")

    def test_a_state_already_stored_is_not_refetched(self):
        blockplace.load_state("29", "MO", fetch=lambda url: _archive(REAL))
        assert "already stored" in self._run("MO")

    def test_it_accepts_a_state_name_as_well_as_a_code(self):
        from django.core.management.base import CommandError

        # Neither should raise "is not a state".
        for spelling in ("MO", "Missouri"):
            try:
                self._run(spelling)
            except CommandError as exc:  # pragma: no cover - network absent
                assert "not a state" not in str(exc)


class TestLoadBookkeeping:
    def test_a_reload_replaces_rather_than_merges(self):
        """A half-old, half-new set of assignments would put one story in
        two cities."""
        blockplace.load_state("29", "MO", fetch=lambda url: _archive(REAL))
        BlockPlaceLoad.objects.filter(state_fips="29").delete()
        blockplace.load_state(
            "29", "MO", fetch=lambda url: _archive([("290190011064015", "99999")])
        )
        assert BlockPlace.objects.count() == 1
        assert blockplace.place_for_block("290190011064015") == "2999999"


class TestTheLadderIsFilledAtEveryReachableRung:
    """Name every level a coding can reach, not just the finest.

    A row carrying only its own label can be shown but not aggregated: a
    block-coded story could not be counted by county, a place-coded one
    not by state, even though both are derivable.

    WHICH RUNGS ARE REACHABLE IS NOT SYMMETRIC, which is why this is a
    function and not three string slices:

        from a BLOCK   state, county from the digits; city by crosswalk
        from a PLACE   state from the digits; city IS the code;
                       COUNTY by crosswalk, because a place can straddle
                       county lines
        from a COUNTY  state, county from the digits; no city
        from a STATE   state only
    """

    @pytest.fixture(autouse=True)
    def loaded(self):
        blockplace.load_state("29", "MO", fetch=lambda url: _archive(REAL))

    def test_a_block_fills_all_three(self):
        rung = blockplace.ladder("290190011064015", "block")
        assert rung["state"] == "Missouri"
        assert rung["county"] == "Boone, MO"
        assert rung["city"] == "Columbia, MO"

    def test_a_place_fills_all_three_too(self):
        """County is the interesting one: it is NOT in a place GEOID's
        digits, because a place can straddle county lines. It comes from
        the place-to-county crosswalk."""
        rung = blockplace.ladder("2932572", "place")
        assert rung["state"] == "Missouri"
        assert rung["city"] == "Holden, MO"
        assert rung["county"] == "Johnson, MO"

    def test_a_county_reaches_state_and_itself_but_no_city(self):
        rung = blockplace.ladder("29101", "county")
        assert rung["state"] == "Missouri"
        assert rung["county"] == "Johnson, MO"
        assert rung["city"] is None
        assert rung["city_geoid"] is None

    def test_a_state_reaches_only_itself(self):
        rung = blockplace.ladder("29", "state")
        assert rung["state"] == "Missouri"
        assert rung["county"] is None
        assert rung["city"] is None

    def test_an_unincorporated_block_still_reaches_county_and_state(self):
        """53% of blocks are in no city. That must not cost them their
        county as well."""
        rung = blockplace.ladder("290019501001000", "block")
        assert rung["city"] is None
        assert rung["county"] == "Adair, MO"
        assert rung["state"] == "Missouri"

    def test_every_rung_carries_a_code_beside_its_name(self):
        """A name is for printing; a code is for grouping. A map that
        groups by name merges two Springfields."""
        rung = blockplace.ladder("290190011064015", "block")
        assert rung["state_geoid"] == "29"
        assert rung["county_geoid"] == "29019"
        assert rung["city_geoid"] == "2915670"

    def test_a_county_name_is_never_a_bare_code(self):
        """`county_label` echoes the FIPS back when it cannot name it,
        which would put a number in a column of names."""
        rung = blockplace.ladder("999999999999999", "block")
        assert rung["county"] is None

    def test_it_uses_the_prepared_lookup(self, django_assert_num_queries):
        blocks = blockplace.places_for_blocks(["290190011064015"])
        with django_assert_num_queries(0):
            rung = blockplace.ladder("290190011064015", "block", blocks=blocks)
        assert rung["city"] == "Columbia, MO"


class TestABlockInNoPlaceIsCalledUnincorporated:
    """ "Unincorporated" is a description of the land, not a placeholder.

    The Block Assignment File is INCPLACE_CDP: it assigns every
    incorporated place AND every Census Designated Place. Missouri's 1,081
    assigned places are 644 cities, 196 villages, 107 towns and 134 CDPs
    -- so unincorporated-but-NAMED communities do get a code. A blank
    PLACEFP means rural ground between towns, which 53% of Missouri's
    blocks are.

    So the dot is labelled for what it is, and the county travels on the
    county rung rather than in the place column. Calling it "Adair, MO"
    would put it beside a dot also called "Adair, MO" that really is a
    county coding, and the two say different things.

    The coordinates are the enrichment's own -- all 60 block-coded rows in
    production carry one, 54 distinct points for 54 distinct blocks -- so
    the dot lands where the story was, not on a centroid.
    """

    def _relabel_block(self):
        from pathlib import Path

        source = Path("visuals/corpus.py").read_text()
        start = source.index("    for row in points:")
        end = source.index("A HUMAN CENTRE IS A DOT", start)
        return source[start:end]

    def test_the_label_is_used_for_a_block_with_no_city(self):
        block = self._relabel_block()
        assert "named = UNINCORPORATED" in block
        assert 'level == "block"' in block

    def test_an_entity_still_wins_over_it(self):
        """A rural block the model named "Pilgrim's Rest Church" should
        say so. Unincorporated is what to call one nobody named."""
        block = self._relabel_block()
        entity = block.index('row.get("place")')
        unincorporated = block.index("named = UNINCORPORATED")
        assert entity < unincorporated

    def test_a_city_still_wins_over_it(self):
        block = self._relabel_block()
        city = block.index('geoid_label(city, "place")')
        unincorporated = block.index("named = UNINCORPORATED")
        assert city < unincorporated

    def test_only_a_block_gets_it(self):
        """A county or state coding is not a statement about
        incorporation, and calling one "Unincorporated" would be a claim
        the data never made."""
        block = self._relabel_block()
        guard = block.index("named = UNINCORPORATED")
        assert 'level == "block"' in block[:guard]

    def test_the_constant_says_what_it_means(self):
        from visuals.corpus import UNINCORPORATED

        assert UNINCORPORATED == "Unincorporated"
