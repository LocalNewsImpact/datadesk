"""An outlet map dated to a month draws what was there that month.

The Barry County Advertiser was collected in March 2026 and closed in June:
it belongs on a March map and on none after. StoneCounty.news launched in
August 2026: not on a March map. The dates are outlet events, and they are
sparse, so where they leave room an outlet is drawn.
"""

import datetime as dt
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from visuals.models import Outlet, OutletEvent
from visuals.outlets import as_of_period, operating, run_outlet_map

D = dt.date


def ev(kind, day, precision="day"):
    return SimpleNamespace(event=kind, effective_date=day, date_precision=precision)


class TestAMonthOrADay:
    def test_a_month(self):
        assert as_of_period("2026-03") == (D(2026, 3, 1), D(2026, 3, 31), "March 2026")

    def test_a_day_stands_for_its_month(self):
        assert as_of_period("2026-02-14") == (
            D(2026, 2, 1),
            D(2026, 2, 28),
            "February 2026",
        )

    def test_empty_is_now(self):
        assert as_of_period("") is None
        assert as_of_period(None) is None

    @pytest.mark.parametrize("bad", ["March 2026", "2026-13", "2026-02-30", "26-03"])
    def test_anything_else_is_refused(self, bad):
        with pytest.raises(ValueError):
            as_of_period(bad)


class TestOperating:
    def test_an_outlet_with_no_dates_was_always_there(self):
        assert operating([], D(2020, 1, 1))

    def test_closed_in_june_is_there_in_march_and_gone_in_july(self):
        closed = [ev("closed", D(2026, 6, 1), "month")]
        assert operating(closed, D(2026, 3, 31))
        assert operating(closed, D(2026, 6, 15))  # June is not over
        assert not operating(closed, D(2026, 7, 1))

    def test_a_closure_dated_only_to_a_year_waits_for_the_year(self):
        closed = [ev("closed", D(2026, 1, 1), "year")]
        assert operating(closed, D(2026, 11, 30))
        assert not operating(closed, D(2027, 1, 1))

    def test_launched_in_august_is_not_there_in_march(self):
        launched = [ev("launched", D(2026, 8, 5))]
        assert not operating(launched, D(2026, 3, 31))
        assert operating(launched, D(2026, 8, 5))

    def test_closed_then_relaunched(self):
        """The Branson Tri-Lakes News: closed July 16, relaunched Aug. 4."""
        history = [ev("closed", D(2026, 7, 16)), ev("relaunched", D(2026, 8, 4))]
        assert operating(history, D(2026, 7, 1))
        assert not operating(history, D(2026, 7, 20))
        assert operating(history, D(2026, 9, 1))

    def test_closed_by_a_date_counts_from_that_date(self):
        closed = [ev("closed", D(2023, 11, 9), "by")]
        assert operating(closed, D(2023, 11, 8))
        assert not operating(closed, D(2023, 11, 9))


pytestmark_db = pytest.mark.django_db(databases=["default", "crawler"])


def _outlet(oid, name, *, on_map=True, status="active", category="not collected"):
    return Outlet.objects.create(
        outlet_id=oid,
        name=name,
        status=status,
        category=category,
        on_map=on_map,
        county_fips="29009",
        lat=36.7,
        lon=-93.9,
        row={},
    )


def _event(user, oid, name, kind, day, precision="day"):
    return OutletEvent.objects.create(
        outlet_id=oid,
        outlet_name=name,
        event=kind,
        effective_date=day,
        date_precision=precision,
        note="test",
        recorded_by=user,
    )


@pytestmark_db
class TestADatedMap:
    @pytest.fixture
    def user(self):
        return User.objects.create_user("reviewer", email="r@localnewsimpact.org")

    @pytest.fixture
    def registry(self, user):
        _outlet(
            "barry",
            "Barry County Advertiser",
            on_map=False,
            status="closed",
            category="",
        )
        _event(
            user, "barry", "Barry County Advertiser", "closed", D(2026, 6, 1), "month"
        )
        _outlet("stone", "StoneCounty.news")
        _event(user, "stone", "StoneCounty.news", "launched", D(2026, 8, 5))
        _outlet("dixon", "The Dixon Pilot", status="replica", category="replica")
        _outlet("gone", "Long Gone", on_map=False, status="closed", category="")
        _outlet(
            "legal", "A Legal Sheet", on_map=False, status="legal", category="legal"
        )

    def names(self, config):
        return {p["name"]: p["category"] for p in run_outlet_map(config)["points"]}

    def test_undated_is_the_registry_now(self, registry):
        assert set(self.names({})) == {"StoneCounty.news", "The Dixon Pilot"}

    def test_march_has_the_advertiser_and_not_the_later_launch(self, registry):
        march = self.names({"as_of": "2026-03"})
        assert "Barry County Advertiser" in march
        assert "StoneCounty.news" not in march

    def test_after_the_closure_the_advertiser_is_gone(self, registry):
        september = self.names({"as_of": "2026-09"})
        assert "Barry County Advertiser" not in september
        assert "StoneCounty.news" in september

    def test_a_closed_outlet_with_no_dates_does_not_come_back(self, registry):
        assert "Long Gone" not in self.names({"as_of": "2026-03"})

    def test_what_is_not_a_newsroom_stays_off(self, registry):
        assert "A Legal Sheet" not in self.names({"as_of": "2026-03"})

    def test_a_reviewed_kind_is_kept(self, registry):
        assert self.names({"as_of": "2026-03"})["The Dixon Pilot"] == "replica"

    def test_the_map_says_its_date(self, registry):
        data = run_outlet_map({"as_of": "2026-03"})
        assert data["meta"]["as_of"] == "March 2026"
        assert "articles in March 2026" in data["points"][0]

    def test_a_retracted_closure_does_not_count(self, registry, user):
        closure = OutletEvent.objects.get(outlet_id="barry")
        OutletEvent.objects.create(
            outlet_id="barry",
            outlet_name="Barry County Advertiser",
            event="retraction",
            retracts=closure,
            note="wrong",
            recorded_by=user,
        )
        # With no dated end left, nothing says it was going in September.
        assert "Barry County Advertiser" not in self.names({"as_of": "2026-09"})


def test_the_sentence_says_the_date():
    from visuals.sentence import parts_for

    visual = SimpleNamespace(
        config={"kind": "outletmap", "as_of": "2026-03", "focus_name": "Missouri"},
        spec={},
    )
    parts = parts_for(visual, "theme")
    assert ("as of", "March 2026", "said") in parts
