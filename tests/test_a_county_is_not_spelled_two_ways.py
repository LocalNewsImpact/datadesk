"""One county, one branch in the newsroom tree.

`sources.county` is free text. Nothing validates it on the way in, so the
Missouri tree carried "Callaway" and "Callaway County" side by side, each
holding one newsroom -- which reads as two counties with one paper apiece
rather than one county with two. 252 of the 253 recorded counties use the
bare name, so the bare name is the convention and the suffix is what
gets dropped.

The other half of the same report was "Nexstar County", a Kansas City TV
station filed under its OWNER. No string tidying turns that into Jackson:
`fox4kc.com` had the owner written into the county column, and the other
sixteen Kansas City sources all say Jackson. That was a data repair on
2026-09-14, not a code change, and these tests say so rather than
pretending the normaliser could have caught it.
"""

from __future__ import annotations

import pytest

from visuals.views import UNRECORDED, _county_key


class TestOneCountyOneKey:
    def test_the_suffix_does_not_make_a_second_county(self):
        """THE BUG."""
        assert _county_key("Callaway County") == _county_key("Callaway")

    @pytest.mark.parametrize(
        "spelled,expected",
        [
            ("Callaway County", "Callaway"),
            ("Orleans Parish", "Orleans"),
            ("Denali Borough", "Denali"),
            ("Juneau City and Borough", "Juneau"),
            ("  Boone  ", "Boone"),
            ("Boone", "Boone"),
        ],
    )
    def test_the_three_words_a_county_is_called(self, spelled, expected):
        assert _county_key(spelled) == expected

    def test_an_empty_county_is_not_a_place(self):
        """A record missing its county is a record the scan already
        flags, not a county called nothing."""
        assert _county_key("") == UNRECORDED
        assert _county_key(None) == UNRECORDED
        assert _county_key("   ") == UNRECORDED


class TestWhatItMustNotTouch:
    def test_an_independent_city_is_not_its_county(self):
        """St. Louis City and St. Louis County are different places with
        different FIPS. Stripping a bare trailing "City" would merge a
        city of 280,000 into the county that surrounds it."""
        assert _county_key("St. Louis City") == "St. Louis City"
        assert _county_key("St. Louis City") != _county_key("St. Louis")

    @pytest.mark.parametrize("name", ["DeKalb", "McDonald"])
    def test_internal_capitals_survive(self, name):
        """Title-casing is the obvious next step and it breaks these two
        real counties -- "Dekalb" and "Mcdonald". Nothing in the data
        needs case folding: no county is spelled two ways once the suffix
        is gone."""
        assert _county_key(name) == name

    def test_a_county_named_for_a_word_in_the_suffix_survives(self):
        """The suffix is anchored to the end and needs whitespace before
        it, so a county whose NAME contains one of those words keeps it."""
        assert _county_key("Borough of Nowhere") == "Borough of Nowhere"
        assert _county_key("Parish Hill") == "Parish Hill"


class TestWhatItDoesNotClaimToFix:
    def test_it_does_not_invent_a_county_from_an_owner(self):
        """`fox4kc.com` carried "Nexstar Media Inc". The normaliser leaves
        it alone on purpose: a value that is not a county cannot be
        rescued by tidying, and pretending otherwise would hide the
        record instead of repairing it."""
        assert _county_key("Nexstar Media Inc") == "Nexstar Media Inc"


class TestTheTreeUsesIt:
    def test_the_grouping_key_is_normalised(self):
        """A normaliser nothing calls is a normaliser that does not
        normalise. The tree must key on it rather than on the raw
        column."""
        from pathlib import Path

        source = Path("visuals/views.py").read_text()
        block = source[source.index("def newsroom_tree_for(scopes):") :]
        block = block[: block.index("\ndef ", 1)]
        assert "_county_key(source.county)" in block
        assert '(source.county or "").strip() or UNRECORDED' not in block
