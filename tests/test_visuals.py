"""The visuals platform: registry, snapshots, publishing, the feed, and
the embed with its stability rule."""

from unittest import mock

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry
from visuals.models import Visual
from visuals.services import publish, refresh_snapshot, unpublish

pytestmark = pytest.mark.django_db

ROWS_V1 = [{"county": "Boone", "stories": 41}]
ROWS_V2 = [{"county": "Boone", "stories": 55}]


@pytest.fixture
def author(django_user_model):
    return django_user_model.objects.create_user(
        "author", email="author@localnewsimpact.org"
    )


@pytest.fixture
def visual(author):
    return Visual.objects.create(
        slug="story-geography",
        title="Story geography",
        source_kind="bigquery",
        query="SELECT county, stories FROM x",
        template="table",
        created_by=author,
    )


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="viewer")
    client.force_login(user)
    return user


def _snapshot(visual, author, rows):
    with mock.patch("explorer.analytics.query_rows", return_value=rows):
        return refresh_snapshot(visual, author)


# --- snapshots and publishing -----------------------------------------------


def test_snapshots_version_up_and_audit(visual, author):
    s1 = _snapshot(visual, author, ROWS_V1)
    s2 = _snapshot(visual, author, ROWS_V2)
    assert (s1.version, s2.version) == (1, 2)
    assert AuditLogEntry.objects.filter(action="visual:snapshot").count() == 2


def test_publish_pins_the_latest_snapshot(visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    visual.refresh_from_db()
    assert visual.status == Visual.PUBLISHED
    assert visual.pinned_snapshot.version == 1
    assert AuditLogEntry.objects.filter(action="visual:publish").exists()


def test_publish_without_a_snapshot_takes_one(visual, author):
    with mock.patch("explorer.analytics.query_rows", return_value=ROWS_V1):
        publish(visual, author)
    visual.refresh_from_db()
    assert visual.pinned_snapshot.version == 1


# --- the embed stability rule ------------------------------------------------


def test_embed_serves_the_pin_while_data_moves_on(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    _snapshot(visual, author, ROWS_V2)  # nightly sync moved the data

    feed = client.get("/visuals/story-geography/data.json").json()
    assert feed["version"] == 1
    assert feed["data"] == ROWS_V1  # the pinned truth, not the new rows


def test_live_requires_explicit_opt_in(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)

    feed = client.get("/visuals/story-geography/data.json?live=1").json()
    assert feed["data"] == ROWS_V1  # allow_live off: ?live=1 is ignored

    Visual.objects.filter(pk=visual.pk).update(allow_live=True)
    with mock.patch("explorer.analytics.query_rows", return_value=ROWS_V2):
        feed = client.get("/visuals/story-geography/data.json?live=1").json()
    assert feed["data"] == ROWS_V2
    assert feed["version"] is None


# --- the public surface (SCOPE.md §3) ----------------------------------------


def test_published_embed_and_feed_are_public(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    embed = client.get("/embed/story-geography/")
    assert embed.status_code == 200
    assert embed["Content-Security-Policy"] == "frame-ancestors 'self'"
    assert "X-Frame-Options" not in embed
    assert client.get("/visuals/story-geography/data.json").status_code == 200


def test_frame_ancestors_come_from_the_visual(client, visual, author):
    Visual.objects.filter(pk=visual.pk).update(
        frame_ancestors="'self' https://www.localnewsimpact.org"
    )
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    visual.refresh_from_db()
    embed = client.get("/embed/story-geography/")
    assert (
        embed["Content-Security-Policy"]
        == "frame-ancestors 'self' https://www.localnewsimpact.org"
    )


def test_drafts_are_absent_for_the_public(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    assert client.get("/embed/story-geography/").status_code == 404
    assert client.get("/visuals/story-geography/data.json").status_code == 404


def test_drafts_preview_for_signed_in_roles(client, viewer, visual, author):
    _snapshot(visual, author, ROWS_V1)
    assert client.get("/embed/story-geography/").status_code == 200
    feed = client.get("/visuals/story-geography/data.json").json()
    assert feed["version"] == 1  # latest snapshot stands in for the pin


def test_full_page_stays_behind_the_sign_in_wall(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    assert client.get("/visuals/story-geography/").status_code == 404


def test_full_page_renders_for_a_viewer(client, viewer, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    response = client.get("/visuals/story-geography/")
    assert response.status_code == 200
    assert "Story geography" in response.content.decode()
    assert "Pinned at snapshot v1" in response.content.decode()


def test_unpublish_returns_to_draft(client, visual, author):
    _snapshot(visual, author, ROWS_V1)
    publish(visual, author)
    unpublish(visual, author)
    assert client.get("/embed/story-geography/").status_code == 404


def test_unknown_renderer_is_a_validation_error(author):
    from django.core.exceptions import ValidationError

    visual = Visual(
        slug="x",
        title="X",
        source_kind="bigquery",
        query="SELECT 1",
        template="does-not-exist",
        created_by=author,
    )
    with pytest.raises(ValidationError):
        visual.full_clean()


def test_the_index_hides_a_visual_from_an_uninvolved_viewer(
    client, viewer, visual, author
):
    """Three ways to see a visual in the admin -- made it, own a dataset
    it is wired to, or are an admin. A viewer who did none of those is
    not one of them, even holding read across the application."""
    response = client.get("/visuals/")
    assert response.status_code == 200
    assert "Story geography" not in response.content.decode()


def test_its_author_sees_their_own_draft(client, visual, author):
    """The author needs a grant to reach the page at all -- somebody with
    none has no standing here -- and then sees their own draft because
    they made it, not because the grant reaches its datasets."""
    Grant.objects.create(user=author, app=DATADESK, scope="", role="viewer")
    client.force_login(author)
    assert "Story geography" in client.get("/visuals/").content.decode()


def test_the_owner_of_a_wired_dataset_sees_it(client, visual, db):
    """Union, not intersection: requiring access to every dataset a
    visual draws on would hide a cross-dataset chart from every owner who
    contributed to it."""
    visual.datasets = ["mizzou", "lehigh"]
    visual.save(update_fields=["datasets"])

    owner = User.objects.create_user("owner", email="owner@localnewsimpact.org")
    Grant.objects.create(user=owner, app=DATADESK, scope="lehigh", role="editor")
    client.force_login(owner)
    assert "Story geography" in client.get("/visuals/").content.decode()


def test_a_viewer_on_a_wired_dataset_does_not(client, visual, db):
    """Owning it and being able to read it are different things."""
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["datasets"])

    reader = User.objects.create_user("reader", email="reader@localnewsimpact.org")
    Grant.objects.create(user=reader, app=DATADESK, scope="mizzou", role="viewer")
    client.force_login(reader)
    assert "Story geography" not in client.get("/visuals/").content.decode()


def test_an_admin_sees_everything(client, visual, db):
    root = User.objects.create_user("boss", email="boss@localnewsimpact.org")
    Grant.objects.create(user=root, app=DATADESK, scope="", role="admin")
    client.force_login(root)
    assert "Story geography" in client.get("/visuals/").content.decode()


def test_index_requires_sign_in(client, visual):
    assert client.get("/visuals/").status_code == 302


def test_a_viewer_sees_every_published_visual(client, viewer, visual):
    """Published is public at its embed and in the bucket, so hiding it
    in the admin protects nothing and only makes it hard to find."""
    visual.status = Visual.PUBLISHED
    visual.save(update_fields=["status"])
    assert "Story geography" in client.get("/visuals/").content.decode()


def test_seeing_a_published_visual_is_not_permission_to_change_it(client, db, visual):
    """Holding `design` says somebody builds visuals; it does not say
    they build *this* one."""
    visual.status = Visual.PUBLISHED
    visual.template = "builder"
    visual.save(update_fields=["status", "template"])

    designer = User.objects.create_user("des", email="des@localnewsimpact.org")
    Grant.objects.create(user=designer, app=DATADESK, scope="", role="designer")
    client.force_login(designer)

    assert "Story geography" in client.get("/visuals/").content.decode()
    assert client.get(f"/visuals/builder/{visual.slug}/").status_code == 403


def test_a_revoked_author_keeps_seeing_and_stops_editing(client, db, visual):
    """ROADMAP item 1: revocation changes who may edit, never what is
    public. The author sees their published visual as any viewer would,
    and only an admin can act on it."""
    visual.status = Visual.PUBLISHED
    visual.template = "builder"
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["status", "template", "datasets"])

    gone = User.objects.create_user("gone", email="gone@localnewsimpact.org")
    Grant.objects.create(user=gone, app=DATADESK, scope="lehigh", role="viewer")
    client.force_login(gone)

    assert "Story geography" in client.get("/visuals/").content.decode()
    assert client.get(f"/visuals/builder/{visual.slug}/").status_code == 403
