"""The status page: what it says when the corpus is moving, and when it is not."""

from datetime import UTC, datetime, timedelta

import pytest
from django.urls import reverse

from datadesk.status import STALE_AFTER, _parse_since, _state

NOW = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


def test_the_window_defaults_to_a_day_when_absent():
    since = _parse_since(None)
    assert timedelta(hours=23) < datetime.now(UTC) - since < timedelta(hours=25)


def test_the_window_is_read_from_the_parameter():
    assert _parse_since("2026-09-16T14:00:06+00:00") == datetime(
        2026, 9, 16, 14, 0, 6, tzinfo=UTC
    )


def test_a_trailing_z_is_a_timestamp_not_junk():
    assert _parse_since("2026-09-16T14:00:06Z") == datetime(
        2026, 9, 16, 14, 0, 6, tzinfo=UTC
    )


def test_junk_falls_back_rather_than_raising():
    """A phone reading a 500 learns nothing about the run."""
    since = _parse_since("last tuesday")
    assert datetime.now(UTC) - since < timedelta(hours=25)


def test_nothing_outstanding_is_done_however_old_the_last_article():
    assert _state(0, NOW - timedelta(days=9), NOW) == "done"


def test_a_recent_article_means_running():
    assert _state(500, NOW - timedelta(minutes=2), NOW) == "running"


def test_silence_past_the_threshold_means_stalled():
    assert _state(500, NOW - STALE_AFTER - timedelta(seconds=1), NOW) == "stalled"


def test_work_outstanding_and_nothing_ever_extracted_is_stalled():
    assert _state(500, None, NOW) == "stalled"


def test_a_naive_timestamp_is_read_as_utc_not_crashed_on():
    """Postgres can hand back a naive datetime; subtracting it must not raise."""
    naive = (NOW - timedelta(minutes=1)).replace(tzinfo=None)
    assert _state(500, naive, NOW) == "running"


@pytest.mark.django_db
def test_the_page_is_behind_a_login(client):
    response = client.get(reverse("status"))
    assert response.status_code == 302
    assert "/accounts/" in response["Location"] or "login" in response["Location"]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_signed_in_reader_gets_the_page(client, django_user_model):
    user = django_user_model.objects.create_user(username="reader", password="x" * 12)
    client.force_login(user)
    response = client.get(reverse("status"))
    assert response.status_code == 200
    assert b"Corpus status" in response.content or b"Entity extraction" in (
        response.content
    )
