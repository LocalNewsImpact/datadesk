"""Snapshot and publish mechanics (SCOPE.md §2.7 v1)."""

import json

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from audit.models import AuditLogEntry
from visuals.dispatch import notify_published
from visuals.models import CORPUS, GCS, INLINE, Visual, VisualSnapshot


class DataSourceError(Exception):
    """The visual's data source could not be read; message is user-facing."""


def may_act_on(user, visual):
    """May this person change this visual -- edit, refresh, publish?

    Seeing and acting are separate. A published visual is visible to
    anyone signed in, because it is already public at its embed; that
    does not make it theirs to change. Acting needs one of:

    - they made it
    - they own a dataset it is wired to
    - they are an application admin

    Which is also what happens after a grant is revoked: the author keeps
    seeing their visual, as any viewer would, and can no longer act on it.
    Only an admin can, which is what ROADMAP item 1 decided.
    """
    from accounts.access import ALL_SCOPES, is_application_admin, permitted_scopes
    from accounts.decorators import APP
    from accounts.privileges import WRITE

    if not user.is_authenticated:
        return False
    if is_application_admin(user, APP):
        return True
    if visual.created_by_id == user.pk:
        return True
    wired = set(visual.datasets or ())
    if not wired:
        return False
    owned = permitted_scopes(user, APP, WRITE)
    if owned is ALL_SCOPES:
        return True
    return bool(wired & set(owned))


def visible_to(user, visual):
    """May this person see this visual inside Datadesk?

    Visibility in the admin follows dataset access, and does so for a
    plainer reason than permission: the admin never puts somebody in front
    of a dataset they do not hold, so a visual drawn from that dataset is
    not something they would be looking at in the first place.

    Three ways in, any one enough (ROADMAP item 1):

    - they can read a dataset it is wired to
    - they made it
    - they are an application admin

    A union, not an intersection. Requiring access to *every* dataset a
    visual draws on would hide a cross-dataset chart from every one of the
    contributors, which is the opposite of what a shared corpus is for.

    **Published does not widen this.** A published visual is public at its
    embed and in the bucket it is exported to -- that is a different
    surface, reached without signing in at all. The admin stays scoped to
    what somebody works with; publishing does not put another team's
    dataset into their console.

    A dataset's public flag (ROADMAP item 10) is what will widen "can
    read" when it exists, and it widens it here for free -- which is the
    point of putting the rule on dataset access rather than on the
    visual's status.
    """
    from accounts.access import ALL_SCOPES, is_application_admin, permitted_scopes
    from accounts.decorators import APP
    from accounts.privileges import READ

    if not user.is_authenticated:
        return False
    if is_application_admin(user, APP):
        return True
    if visual.created_by_id == user.pk:
        return True
    wired = set(visual.datasets or ())
    if not wired:
        return False
    readable = permitted_scopes(user, APP, READ)
    if readable is ALL_SCOPES:
        return True
    return bool(wired & set(readable))


def scopes_of(visual):
    """The datasets a visual may draw on.

    Its own frozen set, never the reader's grants. A published embed is
    read by people with no grants at all, and a snapshot taken later must
    answer the same question the visual has always answered -- so what it
    is wired to travels with it rather than being recomputed from whoever
    happens to be asking.
    """
    return frozenset(visual.datasets or ())


def fetch_source_data(visual):
    """Run the visual's data source and return JSON-compatible rows."""
    if visual.source_kind == INLINE:
        raise DataSourceError(
            "Inline visuals refresh by uploading a new file in the builder."
        )
    if visual.source_kind == CORPUS:
        from visuals.corpus import CorpusSpecError, run_spec, run_story_map

        spec = visual.spec or {}
        try:
            scopes = scopes_of(visual)
            if spec.get("shape") == "story_map":
                return run_story_map(spec, scopes)
            rows, _meta = run_spec(spec, scopes)
        except CorpusSpecError as exc:
            raise DataSourceError(str(exc)) from exc
        return rows
    if visual.source_kind == GCS:
        from google.cloud import storage

        if not visual.bucket_path.startswith("gs://"):
            raise DataSourceError("bucket_path must look like gs://bucket/path")
        bucket_name, _, blob_name = visual.bucket_path[5:].partition("/")
        try:
            blob = storage.Client().bucket(bucket_name).blob(blob_name)
            return json.loads(blob.download_as_bytes())
        except Exception as exc:
            raise DataSourceError(
                f"Could not read {visual.bucket_path}: {exc}"
            ) from exc
    try:
        from explorer.analytics import query_rows

        return query_rows(visual.query)
    except Exception as exc:
        raise DataSourceError(f"BigQuery query failed: {exc}") from exc


def record_snapshot(visual, actor, data, note=""):
    """Store data as the next snapshot version, audited."""
    with transaction.atomic():
        latest = visual.snapshots.aggregate(v=Max("version"))["v"] or 0
        snapshot = VisualSnapshot.objects.create(
            visual=visual, version=latest + 1, data=data, created_by=actor
        )
        AuditLogEntry.objects.create(
            actor=actor,
            action="visual:snapshot",
            target_table="visuals",
            target_ids=[visual.slug],
            after={"version": snapshot.version},
            reason=note or f"snapshot v{snapshot.version} of {visual.slug}",
        )
    return snapshot


def refresh_snapshot(visual, actor):
    """Capture a new snapshot version from the data source."""
    return record_snapshot(visual, actor, fetch_source_data(visual))


def publish(visual, actor):
    """Publish: pin the latest snapshot (taking one if none exists).

    The pin is the embed stability rule — from here the embed serves
    this exact data until a human re-pins.
    """
    snapshot = visual.snapshots.order_by("-version").first()
    if snapshot is None:
        snapshot = refresh_snapshot(visual, actor)
    with transaction.atomic():
        visual.pinned_snapshot = snapshot
        visual.status = Visual.PUBLISHED
        visual.published_at = timezone.now()
        visual.save(
            update_fields=["pinned_snapshot", "status", "published_at", "updated_at"]
        )
        AuditLogEntry.objects.create(
            actor=actor,
            action="visual:publish",
            target_table="visuals",
            target_ids=[visual.slug],
            after={"pinned_version": snapshot.version},
            reason=f"published {visual.slug} at snapshot v{snapshot.version}",
        )
    # Outside the transaction, and best-effort: the pin is already
    # committed, so a GitHub that cannot be reached must not undo it.
    notify_published(visual.slug)
    return visual


def unpublish(visual, actor):
    with transaction.atomic():
        visual.status = Visual.DRAFT
        visual.save(update_fields=["status", "updated_at"])
        AuditLogEntry.objects.create(
            actor=actor,
            action="visual:unpublish",
            target_table="visuals",
            target_ids=[visual.slug],
            reason=f"unpublished {visual.slug}",
        )
    return visual
