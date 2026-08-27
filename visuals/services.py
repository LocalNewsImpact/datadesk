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


#: The one chart kind whose data is a story map rather than a pivot: its
#: two layers come out of the enrichment whole, so it declares no fields
#: and there is nothing for the pivot to group.
STORY_MAP_KIND = "storymap"


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
            # What kind of chart this is, which step one of the walk asks
            # and nothing else decides. `spec["shape"]` says the same
            # thing and is written only by the pivot form on the settings
            # page, so a story map built in the walk had a kind and no
            # shape -- it routed to the pivot, which then refused it for
            # having no dimensions to group by. Choosing a story map is
            # the one way to choose one; the older key is still read so
            # that visuals carrying it keep drawing.
            kind = (visual.config or {}).get("kind")
            if kind == STORY_MAP_KIND or spec.get("shape") == "story_map":
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


def _rows_in(data):
    """How many rows a snapshot holds, whatever shape it is.

    A pivot stores a list. A story map stores an object of layers, each a
    list, and it has drawn something if any of them has anything.
    """
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return sum(len(v) for v in data.values() if isinstance(v, list))
    return 0


class NotPublishable(ValueError):
    """A visual carrying a field that may not be published.

    The message names the fields, because "cannot publish" without saying
    which field stopped it is a dead end -- the author chose several and
    has no way to tell which one is the problem.
    """


def publish(visual, actor):
    """Publish: pin the latest snapshot (taking one if none exists).

    The pin is the embed stability rule — from here the embed serves
    this exact data until a human re-pins.
    """
    # Here rather than in the view: publishing happens from the builder,
    # the admin action, the step panel and a management command, and a
    # rule enforced in four places is a rule enforced in three.
    #
    # Class D of the field audit -- internal use only. Building, viewing
    # and exporting are all untouched; this is the one door it may not go
    # through, and it is shut for republishing too, so a published visual
    # cannot acquire one by editing.
    from visuals.corpus import internal_fields

    # A pinned snapshot of nothing is a published page with nothing on it.
    # cin-composition-by-county was published against two empty snapshots
    # while the same spec returned a hundred rows live: the chart looked
    # broken, the data was fine, and nothing anywhere said which.
    #
    # Checked at the pin rather than at the capture. An empty capture is a
    # fact worth keeping -- it is how "there was nothing that day" is
    # recorded -- but it is not a thing to serve to readers.
    latest = visual.snapshots.order_by("-version").first()
    if latest is not None and not _rows_in(latest.data):
        raise NotPublishable(
            f"{visual.title} has nothing to publish: version "
            f"{latest.version} came back empty. Press Update to run it "
            "again, and publish once it draws."
        )

    blocked = internal_fields(visual.spec)
    if blocked:
        raise NotPublishable(
            f"{visual.title} cannot be published: "
            f"{', '.join(blocked)} "
            f"{'are' if len(blocked) > 1 else 'is'} for internal use. "
            "Everything else still works — look at it here, or take the CSV."
        )
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


def duplicate(visual, actor):
    """A working copy of a visual, so the original can keep serving.

    What is copied is the recipe -- the chart, the slice, the fields, the
    look. What is not is everything that makes the original *the*
    original:

      uuid       a fresh one, because a uuid is a published address. Two
                 visuals sharing one would mean an embed somebody pasted
                 pointing at whichever the database returned first.
      slug       derived, and deduped, for the same reason.
      status     draft. A copy of a published visual that arrived
                 published would put an unreviewed chart on the data host
                 the moment it was made.
      snapshots  none, and so no pin -- for anything with a source to
                 re-run. They are captured runs of it, and the copy has
                 not run it yet; carrying them would date the copy's data
                 to the original's last refresh and say so on the page.

                 Uploaded data is the exception, and it has to be. There
                 is no source behind it: the rows *are* the snapshot, so a
                 copy without one is a visual that can never draw anything
                 and has no way to be given data. That one carries the
                 pinned rows across as its own first snapshot.

    `created_by` is whoever asked for the copy, not whoever wrote the
    original. They are the one who has to answer for it now.
    """
    from django.utils.text import slugify

    base = slugify(f"{visual.slug}-copy")[:40] or "visual-copy"
    slug, n = base, 2
    while Visual.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1

    copy = Visual.objects.create(
        slug=slug,
        title=f"{visual.title} (copy)",
        status=Visual.DRAFT,
        source_kind=visual.source_kind,
        query=visual.query,
        bucket_path=visual.bucket_path,
        template=visual.template,
        config=dict(visual.config or {}),
        spec=dict(visual.spec or {}),
        datasets=list(visual.datasets or []),
        allow_live=visual.allow_live,
        frame_ancestors=visual.frame_ancestors,
        created_by=actor,
    )
    if visual.source_kind == INLINE:
        source = visual.pinned_snapshot or visual.snapshots.order_by("-version").first()
        if source is not None:
            snapshot = VisualSnapshot.objects.create(
                visual=copy, version=1, data=source.data, created_by=actor
            )
            copy.pinned_snapshot = snapshot
            copy.save(update_fields=["pinned_snapshot"])

    AuditLogEntry.objects.create(
        actor=actor,
        action="visual:duplicate",
        target_table="visuals",
        target_ids=[copy.slug],
        after={"copied_from": visual.slug},
        reason=f"{copy.slug} copied from {visual.slug}",
    )
    return copy
