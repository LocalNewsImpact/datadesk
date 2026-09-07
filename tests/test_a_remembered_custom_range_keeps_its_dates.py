"""Returning to the queue dropped the date filter, and the page stopped
answering.

`/review/queue/` restores the filters from the last visit by redirecting
to them. `QUEUE_FILTER_KEYS` decides what is remembered and it did not
include `since` or `until` -- so a custom March range came back as
`days=custom` with no bounds, and `_between_two_dates` read a range with
neither bound as the whole corpus.

That was survivable while the flagged set was small. It is 60,408 rows
now that wire, weather, opinion and paywall have cases -- wire alone is
47,419 -- and `days=custom` also counts as an explicit filter, so the
landing-view narrowing is off as well. The queue answered with everything
and timed out.

The fix is remembering the bounds, not reinterpreting an empty range.
Reading a custom range with neither bound as the whole corpus is
deliberate and tested (test_the_queue_reads_and_filters); a half-typed
date must not narrow anything either. Those stay as they are, and the
state that made them dangerous no longer arises.
"""

from datetime import UTC, datetime

import pytest

from explorer.models import Article, CandidateLink
from review import queue as q
from review.views import QUEUE_FILTER_KEYS


def test_the_bounds_of_a_custom_range_are_remembered():
    """Without these the remembered filter is `days=custom` alone, which
    is a range with no range."""
    assert "since" in QUEUE_FILTER_KEYS
    assert "until" in QUEUE_FILTER_KEYS


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_custom_range_with_bounds_still_uses_them(crawler_schema):
    """The fallback must not swallow a range a reader did fill in --
    March 2026 is outside the default window and is the range this queue
    is worked in."""
    link = CandidateLink.objects.create(id="cl-march", url="https://a.example/m")
    Article.objects.create(
        id="march",
        candidate_link=link,
        status="wire",
        wire_check_status="complete",
        title="A March story",
        publish_date=datetime(2026, 3, 15, tzinfo=UTC),
    )

    qs = q._within_the_window(
        q.base_queryset_unscoped(),
        {"days": q.CUSTOM, "since": "2026-03-01", "until": "2026-04-01"},
    )

    assert set(qs.values_list("id", flat=True)) == {"march"}
