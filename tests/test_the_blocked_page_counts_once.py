"""The Blocked page reads a count; it never takes one.

The report asks twenty-one questions of the crawler and they cost about
97 seconds together — several are sequential scans of a 1.5 GB articles
table and an 838 MB telemetry table. Indexing what could be indexed took
the worst from 375s to 5.8s and did not change the shape of the problem.

Two faults, in order of discovery:

1. The view called `grouped()`, which ran all 21, and then
   `blocked_total()`, which ran the same 21 again for a number it could
   have summed from what `grouped()` already had. 195 seconds.
2. Even one pass is 97 seconds, which is a batch job and not a page.
   Cloud Run cuts a request at 300 and the page returned 504 either way.

So the counting moved to `refresh_blocked_inventory` on a schedule and
the page renders the newest row. These tests assert the page does no
counting at all, which is the property that keeps it up — a timing
assertion would pass on a laptop against an empty test database whatever
the page did.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from explorer import blocked
from explorer.models import BlockedInventory

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

ROWS = [
    {
        "group": blocked.FETCH_FAILED,
        "label": "404 not found",
        "why": "gone",
        "count": 3,
        "publishers": [],
    },
    {
        "group": blocked.WAITING,
        "label": "queued",
        "why": "",
        "count": 99,
        "publishers": [],
    },
]


@pytest.fixture
def viewer(db):
    user = User.objects.create_user("bl", email="bl@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    return user


def test_the_page_never_counts(client, viewer, crawler_schema):
    """The one property that keeps the page under the request ceiling."""
    BlockedInventory.objects.create(rows=ROWS)
    client.force_login(viewer)
    with patch("explorer.blocked.inventory") as counting:
        response = client.get(reverse("explorer:blocked"))
    counting.assert_not_called()
    assert response.status_code == 200


def test_the_page_shows_the_newest_count(client, viewer, crawler_schema):
    BlockedInventory.objects.create(rows=[])
    BlockedInventory.objects.create(rows=ROWS)
    client.force_login(viewer)
    body = client.get(reverse("explorer:blocked")).content.decode()
    # 3, not 102: waiting is a backlog, not a blockage.
    assert "404 not found" in body
    assert "Counted" in body, "the page does not say how old the count is"


def test_no_count_yet_does_not_read_as_nothing_blocked(client, viewer, crawler_schema):
    """An empty page and a page reporting zero problems look identical,
    and one of them is a lie."""
    assert not BlockedInventory.objects.exists()
    client.force_login(viewer)
    body = client.get(reverse("explorer:blocked")).content.decode()
    assert "No count has been taken yet" in body


# ------------------------------------------------------- the count itself


def test_counting_writes_one_snapshot(crawler_schema):
    from django.core.management import call_command

    with patch("explorer.blocked.inventory", return_value=ROWS) as counting:
        call_command("refresh_blocked_inventory")
    assert counting.call_count == 1, "the inventory ran more than once"
    snapshot = BlockedInventory.objects.get()
    assert snapshot.rows == ROWS


def test_an_unreachable_crawler_writes_nothing(crawler_schema):
    """`inventory()` answers None when the crawler is gone. Storing that
    would replace an hour-old number with a blank one, and the page would
    report nothing blocked — worse than being stale."""
    from django.core.management import call_command

    BlockedInventory.objects.create(rows=ROWS)
    with patch("explorer.blocked.inventory", return_value=None):
        call_command("refresh_blocked_inventory")
    assert BlockedInventory.objects.count() == 1
    assert BlockedInventory.objects.get().rows == ROWS


def test_old_snapshots_are_pruned(crawler_schema):
    from django.core.management import call_command
    from django.utils import timezone

    old = BlockedInventory.objects.create(rows=ROWS)
    BlockedInventory.objects.filter(pk=old.pk).update(
        computed_at=timezone.now() - timezone.timedelta(days=60)
    )
    with patch("explorer.blocked.inventory", return_value=ROWS):
        call_command("refresh_blocked_inventory")
    assert not BlockedInventory.objects.filter(pk=old.pk).exists()
    assert BlockedInventory.objects.count() == 1


# ------------------------------------------- both readings, one set of rows


def test_both_readings_come_from_the_same_rows(crawler_schema):
    with patch("explorer.blocked.inventory") as never:
        assert blocked.blocked_total(ROWS) == 3
        groups = blocked.grouped(ROWS)
        never.assert_not_called()
    assert [g["group"] for g in groups] == [blocked.FETCH_FAILED, blocked.WAITING]


def test_an_unreachable_crawler_still_reads_as_unreachable(crawler_schema):
    """None is a legal thing to pass and must not be mistaken for
    "no rows given"."""
    with patch("explorer.blocked.inventory") as never:
        assert blocked.grouped(None) is None
        assert blocked.blocked_total(None) is None
        never.assert_not_called()


def test_the_count_is_wired_into_the_daily_job():
    """What keeps it running.

    The page cannot count for itself, so a snapshot nobody refreshes is a
    page that goes quietly stale and then reads as though nothing is
    blocked. `daily_housekeeping` is where this belongs rather than a job
    of its own -- that command's own words: "a second Cloud Run job and a
    second Cloud Scheduler entry per task is how a task comes to have
    neither."
    """
    from explorer.management.commands.daily_housekeeping import TASKS

    assert "refresh_blocked_inventory" in [name for name, _, _ in TASKS]
