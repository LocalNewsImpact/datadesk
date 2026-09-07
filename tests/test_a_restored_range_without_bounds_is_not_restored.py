"""A session saved before the bounds were remembered redirects its owner
into a four-minute page, on every visit, forever.

`/review/queue/` restores the last visit's filters by redirecting to
them. Until 2026-09-07 `since` and `until` were not among the remembered
keys, so a custom March range was saved as `days=custom` alone -- and a
custom range with neither bound reads as the whole corpus: 60,169 flagged
rows for Mizzou, counted twice before the page can draw. Production
measured one such request at 254 seconds.

Remembering the bounds stopped that state being created. It did not stop
it being restored: sessions outlive deploys, which is the point of them,
and every session holding the old shape walks back into the hang.

Reading an unfilled custom range as the whole corpus stays as it is --
deliberate, tested, and a reasonable answer to a question somebody asked.
What is not reasonable is restoring somebody into it.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from review.views import QUEUE_FILTERS, _without_an_unbounded_range


def test_a_range_with_no_bounds_is_dropped():
    remembered = {"days": "custom", "dataset": "Mizzou-Missouri-State"}

    assert _without_an_unbounded_range(remembered) == {
        "dataset": "Mizzou-Missouri-State"
    }


def test_a_range_with_bounds_survives():
    remembered = {"days": "custom", "since": "2026-03-01", "until": "2026-04-01"}

    assert _without_an_unbounded_range(remembered) == remembered


def test_one_bound_is_enough():
    """ "Since the template changed" is a real question with no upper
    bound, and `_between_two_dates` answers it."""
    remembered = {"days": "custom", "since": "2026-03-01"}

    assert _without_an_unbounded_range(remembered) == remembered


def test_a_named_window_is_untouched():
    remembered = {"days": "30", "dataset": "mo"}

    assert _without_an_unbounded_range(remembered) == remembered


def test_nothing_remembered_stays_nothing():
    assert _without_an_unbounded_range(None) is None
    assert _without_an_unbounded_range({}) == {}


def test_a_range_alone_leaves_nothing_to_restore():
    """Dropping the only key means there is no position to send anybody
    back to, which must read as no memory rather than an empty redirect."""
    assert _without_an_unbounded_range({"days": "custom"}) is None


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_stale_session_does_not_redirect_into_the_corpus(client, crawler_schema):
    """End to end: the shape every pre-fix session holds."""
    user = User.objects.create_user("stale", email="stale@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)

    session = client.session
    session[QUEUE_FILTERS] = {"days": "custom", "dataset": "missouri"}
    session.save()

    response = client.get(reverse("review:queue"))

    if response.status_code == 302:
        assert (
            "days=custom" not in response["Location"]
        ), "restored into an unbounded range, which is the 254-second page"
