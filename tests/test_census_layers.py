"""Census layers: the measures a map can shade by, fetched and kept.

Twenty-eight ACS measures under plain names, each saying whether it holds at
tract level; fetched in batches under the API's limit; kept with their
margins so a map can hatch what the survey cannot say (docs/LAYERED_MAP.md).
"""

import re
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from visuals import census
from visuals.models import CensusValue

pytestmark = pytest.mark.django_db


class TestTheRegistry:
    def test_twenty_eight_measures_under_unique_keys(self):
        keys = [v["key"] for v in census.VARIABLES]
        assert len(keys) == 28 and len(set(keys)) == 28

    def test_every_code_is_a_profile_estimate(self):
        for v in census.VARIABLES:
            for code in v["codes"]:
                assert re.fullmatch(r"DP0[2-5]_\d{4}E", code), (v["key"], code)
            assert v["kind"] in ("share", "value")

    def test_a_tract_map_is_offered_only_what_holds_there(self):
        """Nine measures were unreliable in more than a third of Missouri's
        tracts on 2026-09-30: offered by county only."""
        county_only = {v["key"] for v in census.VARIABLES if not v["tract"]}
        assert county_only == {
            "black_not_hispanic",
            "hispanic",
            "foreign_born",
            "language_other_than_english",
            "veterans",
            "unemployment_rate",
            "below_poverty",
            "no_health_insurance",
            "vacant_housing",
        }
        assert len(census.offered("county")) == 28
        assert len(census.offered("tract")) == 19

    def test_a_share_needs_four_columns_and_a_value_two(self):
        assert census.columns(census.BY_KEY["under_18"]) == [
            "DP05_0019E",
            "DP05_0019M",
            "DP05_0019PE",
            "DP05_0019PM",
        ]
        assert census.columns(census.BY_KEY["median_age"]) == [
            "DP05_0018E",
            "DP05_0018M",
        ]
        assert len(census.columns(census.BY_KEY["built_before_1980"])) == 20


class TestReliability:
    def test_a_wide_margin_is_unreliable(self):
        assert census.unreliable(100, 60)  # CV 36%
        assert not census.unreliable(100, 40)  # CV 24%

    def test_nothing_counted_is_not_unreliable(self):
        assert census.unreliable(0, 12) is False

    def test_no_estimate_is_not_judged(self):
        assert census.unreliable(None, 5) is None
        assert census.unreliable(50, None) is False


def _api(header, *rows):
    return [header, *rows]


def fake_get(calls):
    """A Census API that answers every column asked for, one county."""

    def get(url):
        calls.append(url)
        asked = re.search(r"get=([^&]+)", url).group(1).split(",")
        assert len(asked) <= 50, "the API takes at most 50 columns"
        values = []
        for col in asked:
            if col.endswith("PE"):
                values.append("12.5")
            elif col.endswith("PM"):
                values.append("3.0")
            elif col.endswith("M"):
                values.append("40")
            else:
                values.append("1000")
        return _api([*asked, "state", "county"], [*values, "29", "173"])

    return get


class TestFetch:
    def test_every_variable_comes_back_for_every_place(self):
        calls = []
        rows = census.fetch("county", get=fake_get(calls))
        assert len(rows) == 28 and {r["geoid"] for r in rows} == {"29173"}
        assert all(
            len(re.search(r"get=([^&]+)", c).group(1).split(",")) <= 50 for c in calls
        )
        assert len(calls) >= 2  # 110 columns do not fit one call

    def test_a_share_keeps_the_census_percent_and_a_value_does_not(self):
        rows = {r["variable"]: r for r in census.fetch("county", get=fake_get([]))}
        assert (
            rows["under_18"]["percent"] == 12.5
            and rows["under_18"]["percent_moe"] == 3.0
        )
        assert rows["median_age"]["percent"] is None
        assert (
            rows["median_age"]["estimate"] == 1000 and rows["median_age"]["moe"] == 40
        )

    def test_decades_are_summed_and_their_margins_rooted(self):
        row = next(
            r
            for r in census.fetch("county", get=fake_get([]))
            if r["variable"] == "built_before_1980"
        )
        assert row["estimate"] == 5000 and row["percent"] == 62.5
        assert round(row["moe"], 2) == round((5 * 40**2) ** 0.5, 2)

    def test_the_api_s_missing_markers_are_nothing(self):
        def get(url):
            asked = re.search(r"get=([^&]+)", url).group(1).split(",")
            return _api(
                [*asked, "state", "county"], [*["-888888888"] * len(asked), "29", "173"]
            )

        rows = census.fetch("county", variables=[census.BY_KEY["median_age"]], get=get)
        assert rows == [
            {
                "geoid": "29173",
                "variable": "median_age",
                "estimate": None,
                "moe": None,
                "percent": None,
                "percent_moe": None,
            }
        ]

    def test_a_tract_is_asked_for_and_keyed_by_its_eleven_digits(self):
        def get(url):
            assert "for=tract:*&in=state:29" in url
            asked = re.search(r"get=([^&]+)", url).group(1).split(",")
            return _api(
                [*asked, "state", "county", "tract"],
                [*["1"] * len(asked), "29", "001", "950100"],
            )

        rows = census.fetch("tract", variables=[census.BY_KEY["median_age"]], get=get)
        assert rows[0]["geoid"] == "29001950100"

    def test_without_a_key_the_real_api_is_not_called(self, monkeypatch):
        monkeypatch.delenv("CENSUS_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="CENSUS_API_KEY"):
            census.fetch("county")

    def test_an_unknown_level_is_refused(self):
        with pytest.raises(ValueError):
            census.fetch("state", get=fake_get([]))


class TestStore:
    def test_a_second_fetch_replaces_not_duplicates(self):
        rows = census.fetch(
            "county", variables=[census.BY_KEY["under_18"]], get=fake_get([])
        )
        assert census.store(rows, "county") == {"rows": 1, "before": 0}
        rows[0]["percent"] = 20.0
        assert census.store(rows, "county") == {"rows": 1, "before": 1}
        assert CensusValue.objects.count() == 1
        assert CensusValue.objects.get().percent == 20.0

    def test_reliability_is_read_from_what_is_stored(self):
        CensusValue.objects.create(
            year=2024,
            level="tract",
            geoid="29001950100",
            variable="foreign_born",
            estimate=4,
            moe=6,
            percent=0.1,
            percent_moe=0.2,
        )
        CensusValue.objects.create(
            year=2024,
            level="tract",
            geoid="29001950200",
            variable="foreign_born",
            estimate=400,
            moe=60,
            percent=10,
            percent_moe=1,
        )
        report = {r["key"]: r for r in census.reliability("tract")}
        assert report["foreign_born"] == {
            "key": "foreign_born",
            "label": "Foreign born",
            "places": 2,
            "hatched": 1,
            "share": 0.5,
        }
        assert (
            report["median_age"]["places"] == 0
            and report["median_age"]["share"] is None
        )


class TestTheCommand:
    def test_it_fetches_and_says_what_it_stored(self, monkeypatch):
        monkeypatch.setattr(census, "_get", fake_get([]))
        monkeypatch.setenv("CENSUS_API_KEY", "k")
        out = StringIO()
        call_command("fetch_census_layers", "--level", "county", stdout=out)
        assert (
            "28 values stored for county 29 (2024 ACS 5-year); 0 were stored before"
            in out.getvalue()
        )
        assert CensusValue.objects.count() == 28

    def test_a_subset_and_an_unknown_key(self, monkeypatch):
        monkeypatch.setattr(census, "_get", fake_get([]))
        monkeypatch.setenv("CENSUS_API_KEY", "k")
        call_command(
            "fetch_census_layers",
            "--level",
            "county",
            "--variables",
            "median_age,under_18",
            stdout=StringIO(),
        )
        assert CensusValue.objects.count() == 2
        with pytest.raises(CommandError, match="unknown variables: nope"):
            call_command(
                "fetch_census_layers", "--level", "county", "--variables", "nope"
            )

    def test_without_a_key_it_says_so(self, monkeypatch):
        monkeypatch.delenv("CENSUS_API_KEY", raising=False)
        with pytest.raises(CommandError, match="CENSUS_API_KEY"):
            call_command("fetch_census_layers", "--level", "county")

    def test_report_reads_the_store(self):
        CensusValue.objects.create(
            year=2024,
            level="county",
            geoid="29173",
            variable="median_age",
            estimate=46,
            moe=1,
        )
        out = StringIO()
        call_command("fetch_census_layers", "--level", "county", "--report", stdout=out)
        assert re.search(r"Median age\s+1 places\s+0 unreliable\s+0%", out.getvalue())
