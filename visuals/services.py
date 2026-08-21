"""Snapshot and publish mechanics (SCOPE.md §2.6 v1)."""

import json

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from audit.models import AuditLogEntry
from visuals.models import GCS, Visual, VisualSnapshot


class DataSourceError(Exception):
    """The visual's data source could not be read; message is user-facing."""


def fetch_source_data(visual):
    """Run the visual's data source and return JSON-compatible rows."""
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


def refresh_snapshot(visual, actor):
    """Capture a new snapshot version from the data source."""
    data = fetch_source_data(visual)
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
            reason=f"snapshot v{snapshot.version} of {visual.slug}",
        )
    return snapshot


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
