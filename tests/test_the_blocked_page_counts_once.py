"""The Blocked page runs its inventory once.

The page asks 21 questions of the crawler and they cost 97 seconds
together. `grouped()` ran them, and then `blocked_total()` ran the same
21 again for a number it could have summed from what `grouped()` already
had -- 195 seconds, past Cloud Run's 300-second request ceiling once the
network is added, so the page returned 504 on every visit.

Indexing the slowest columns is what made the queries survivable and it
did not fix this: half of nothing is still nothing. The two are separate
faults and this is the one that kept the page down.

The test counts calls rather than seconds. A timing assertion would pass
on a fast laptop and on an empty test database whatever the page did.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def viewer(db):
    user = User.objects.create_user("bl", email="bl@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    return user


def test_the_page_takes_one_inventory(client, viewer, crawler_schema):
    client.force_login(viewer)
    with patch("explorer.blocked.inventory", return_value=[]) as taken:
        client.get(reverse("explorer:blocked"))
    assert taken.call_count == 1, (
        f"the inventory ran {taken.call_count} times; each pass is 21 "
        "queries and about 97 seconds against production"
    )


def test_both_readings_come_from_the_same_rows(crawler_schema):
    """Passing the rows in is what makes one pass possible, so the two
    readings have to accept them."""
    from explorer import blocked

    rows = [
        {
            "group": blocked.FETCH_FAILED,
            "label": "a",
            "why": "",
            "count": 3,
            "publishers": [],
        },
        {
            "group": blocked.WAITING,
            "label": "b",
            "why": "",
            "count": 99,
            "publishers": [],
        },
    ]
    with patch("explorer.blocked.inventory") as never:
        assert blocked.blocked_total(rows) == 3
        groups = blocked.grouped(rows)
        never.assert_not_called()
    assert [g["group"] for g in groups] == [blocked.FETCH_FAILED, blocked.WAITING]


def test_an_unreachable_crawler_still_reads_as_unreachable(crawler_schema):
    """`inventory()` answers None when the crawler is gone, and None is a
    legal thing to pass. It must not be mistaken for "no rows given"."""
    from explorer import blocked

    with patch("explorer.blocked.inventory") as never:
        assert blocked.grouped(None) is None
        assert blocked.blocked_total(None) is None
        never.assert_not_called()
