"""A figure that changes daily should not need republishing daily.

Two ways to stay current already existed and neither fitted. `allow_live`
runs the source on every request, so nothing is cached and each view
costs a query -- right for "what does it say this second", wrong for a
number that moves once a day, which is most of them here: the queries
follow a nightly BigQuery sync. Republishing by hand fitted the data and
not the person.

`keep_updated` is the third: keep the snapshot, the version history and
the full hour of caching, and let the scheduled refresh move the pin --
but only when the data differs from what is pinned.

It is off by default and per visual because it is an exception to the
embed-stability rule: a published report must not change under its
readers, and this says that for THIS report, changing is the point.
"""

from io import StringIO
from unittest import mock

import pytest
from django.core.management import call_command

from visuals.models import Visual
from visuals.services import publish, refresh_snapshot

pytestmark = pytest.mark.django_db


@pytest.fixture
def author(django_user_model):
    user = django_user_model.objects.create_user(
        "keeper", email="keeper@localnewsimpact.org", is_superuser=True
    )
    return user


@pytest.fixture
def visual(author):
    return Visual.objects.create(
        slug="daily-figure",
        title="A daily figure",
        source_kind="bigquery",
        query="SELECT n FROM x",
        template="table",
        created_by=author,
    )


def _refresh(visual, author, rows):
    with mock.patch("explorer.analytics.query_rows", return_value=rows):
        return refresh_snapshot(visual, author)


def _run(rows, **kwargs):
    out = StringIO()
    with mock.patch("explorer.analytics.query_rows", return_value=rows):
        call_command(
            "publish_visuals",
            actor="keeper@localnewsimpact.org",
            stdout=out,
            **kwargs,
        )
    return out.getvalue()


def _published(visual, author, rows):
    _refresh(visual, author, rows)
    publish(visual, author)
    visual.refresh_from_db()
    return visual


def test_off_by_default(visual):
    """An exception to the embed-stability rule is opted into, never
    inherited."""
    assert visual.keep_updated is False


def test_the_schedule_moves_the_pin_when_the_data_changed(visual, author):
    visual = _published(visual, author, [{"n": 1}])
    was = visual.pinned_snapshot.version
    Visual.objects.filter(pk=visual.pk).update(keep_updated=True)

    _run([{"n": 2}])

    visual.refresh_from_db()
    assert visual.pinned_snapshot.version > was
    assert visual.pinned_snapshot.data == [{"n": 2}]


def test_identical_data_does_not_move_the_pin(visual, author):
    """A daily republish of the same rows moves the pin, writes an audit
    entry and throws away an hour of cache to deliver the same numbers --
    and buries the entries that meant something."""
    visual = _published(visual, author, [{"n": 1}])
    was = visual.pinned_snapshot.version
    Visual.objects.filter(pk=visual.pk).update(keep_updated=True)

    output = _run([{"n": 1}])

    visual.refresh_from_db()
    assert visual.pinned_snapshot.version == was
    assert "same data" in output


def test_a_snapshot_is_still_taken_either_way(visual, author):
    """The pin not moving is not a reason to stop recording history."""
    visual = _published(visual, author, [{"n": 1}])
    before = visual.snapshots.count()
    Visual.objects.filter(pk=visual.pk).update(keep_updated=True)

    _run([{"n": 1}])

    assert visual.snapshots.count() == before + 1


def test_without_the_flag_the_pin_stays_put(visual, author):
    """The rule this is an exception to, still holding for everything
    that has not opted out of it."""
    visual = _published(visual, author, [{"n": 1}])
    was = visual.pinned_snapshot.version

    output = _run([{"n": 2}])

    visual.refresh_from_db()
    assert visual.pinned_snapshot.version == was
    assert "embeds still serve" in output


def test_repin_still_overrides_for_one_run(visual, author):
    """The operator saying so for this run, without editing a visual."""
    visual = _published(visual, author, [{"n": 1}])
    was = visual.pinned_snapshot.version

    _run([{"n": 2}], repin=True)

    visual.refresh_from_db()
    assert visual.pinned_snapshot.version > was


def test_the_dry_run_says_which_it_would_do(visual, author):
    """A dry run that reports the same sentence for both settings does
    not answer the question it was asked."""
    _published(visual, author, [{"n": 1}])
    assert "would refresh (" in _run([{"n": 1}], dry_run=True)

    Visual.objects.filter(slug="daily-figure").update(keep_updated=True)
    assert "would refresh and repin" in _run([{"n": 1}], dry_run=True)


# --- reachable from the console, and true when used --------------------------


@pytest.mark.urls("datadesk.urls_data")
def test_the_public_page_honours_live(client, visual, author):
    """`?live=1` reached the feed and not the page that draws it.

    So on the data host -- the one host where `allow_live` is the only
    thing that can grant live at all -- the parameter was accepted,
    ignored, and the pinned snapshot drawn. A documented switch that
    silently does nothing is worse than not having it.
    """
    visual = _published(visual, author, [{"n": 1}])
    Visual.objects.filter(pk=visual.pk).update(allow_live=True)

    page = client.get(f"/visuals/{visual.uuid}/?live=1")
    assert page.status_code == 200
    body = page.content.decode()
    assert "live=1" in body, "the page must ask its feed for live data"
    # And it must not be cached, or the first reader's copy freezes it
    # for everyone after them.
    assert page["Cache-Control"] == "no-store"


@pytest.mark.urls("datadesk.urls_data")
def test_the_public_page_without_live_is_still_pinned_and_cached(
    client, visual, author
):
    """The plain URL is unchanged: a reader who did not ask for live gets
    the pin, and the hour of caching that makes an embed cheap."""
    visual = _published(visual, author, [{"n": 1}])
    Visual.objects.filter(pk=visual.pk).update(allow_live=True)

    page = client.get(f"/visuals/{visual.uuid}/")
    assert "live=1" not in page.content.decode()
    assert "no-store" not in page["Cache-Control"]


@pytest.mark.urls("datadesk.urls_data")
def test_live_on_the_page_still_needs_permission(client, visual, author):
    """The same rule the feed applies. Asking is not granting."""
    visual = _published(visual, author, [{"n": 1}])
    assert visual.allow_live is False

    page = client.get(f"/visuals/{visual.uuid}/?live=1")
    assert "live=1" not in page.content.decode()


def test_the_publish_step_offers_both_settings(client, visual, author):
    """Both lived only in the Django admin, which is the same as nowhere:
    the person who builds a visual does not go to /admin/ to finish it."""
    from visuals.panels import publish_panel

    context = publish_panel(visual)
    assert context["keep_updated"] is False
    assert context["allow_live"] is False


def test_the_step_writes_both_and_audits_it(visual, author):
    """Audited like publishing, because it is the same kind of fact: it
    changes what a public URL serves, and when."""
    from audit.models import AuditLogEntry
    from visuals.panels import publish_panel

    publish_panel(visual, {"do": "freshness", "keep_updated": "on"}, actor=author)
    visual.refresh_from_db()
    assert visual.keep_updated is True
    assert visual.allow_live is False  # unchecked means off, not unchanged

    entry = AuditLogEntry.objects.filter(action="visual:freshness").first()
    assert entry is not None
    assert entry.after == {"keep_updated": True, "allow_live": False}


def test_unchanged_settings_write_no_audit_entry(visual, author):
    """Saving a form nobody changed is not an event."""
    from audit.models import AuditLogEntry
    from visuals.panels import publish_panel

    publish_panel(visual, {"do": "freshness"}, actor=author)
    assert not AuditLogEntry.objects.filter(action="visual:freshness").exists()
