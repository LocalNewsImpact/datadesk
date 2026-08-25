"""The visuals registry (SCOPE.md §2.7 v1).

A Visual is authored as code — a renderer template in the repo plus a
data source (a named BigQuery query or a bucket object) — and registered
and published through the admin. Publishing pins a data snapshot; embeds
serve the pinned version by default with an explicit opt-in to live data
(the embed stability rule: a published report must not change under its
readers).
"""

import uuid as uuid_lib

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.template.loader import TemplateDoesNotExist, get_template

BIGQUERY = "bigquery"
GCS = "gcs"
INLINE = "inline"
CORPUS = "corpus"


def _validate_renderer(name):
    try:
        get_template(f"visuals/renderers/{name}.html")
    except TemplateDoesNotExist as exc:
        raise ValidationError(
            f"No renderer template at visuals/renderers/{name}.html — "
            "visuals are authored as code; add the template first."
        ) from exc


class Visual(models.Model):
    DRAFT = "draft"
    PUBLISHED = "published"
    STATUSES = [(s, s) for s in (DRAFT, PUBLISHED)]
    SOURCE_KINDS = [
        (BIGQUERY, "BigQuery query"),
        (GCS, "bucket object"),
        (INLINE, "uploaded data"),
        (CORPUS, "the research corpus"),
    ]

    # What a published URL is built from, and the only identifier that can
    # carry that promise. An embed URL is pasted into somebody else's
    # article and can never be moved afterwards, so the thing naming it
    # must be unable to change -- and `slug` can: it is unique, editable,
    # and generated from the title, so a rename in the admin silently
    # breaks every embed already in the wild and tells nobody.
    #
    # The slug stays, for reading and for the URLs already handed out. It
    # is now a label rather than an address.
    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)

    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUSES, default=DRAFT)

    source_kind = models.CharField(max_length=20, choices=SOURCE_KINDS)
    # BigQuery: the SELECT the feed runs. GCS: gs://bucket/path to a JSON
    # object. Exactly one applies, enforced in clean().
    query = models.TextField(blank=True, default="")
    bucket_path = models.CharField(max_length=500, blank=True, default="")

    # The renderer template's name under templates/visuals/renderers/.
    # Builder visuals use "builder"; hand-authored visuals name their own.
    template = models.CharField(max_length=100, validators=[_validate_renderer])

    # The form-driven builder's chart definition (SCOPE.md §2.7 v2): kind,
    # column mappings, and options, read by the builder renderer runtime.
    # Empty for hand-authored visuals.
    config = models.JSONField(default=dict, blank=True)

    # The datasets this visual is wired to, frozen when it is saved
    # (ROADMAP item 1). Slugs rather than keys: datasets live in the
    # crawler's database and Django has no cross-database foreign keys,
    # which is the same reason `Grant.scope` is a slug.
    #
    # Frozen, not recomputed. A visual is a claim about particular data at
    # a particular time; recomputing on read would let a revoked grant
    # silently change what the visual covers, and a published embed would
    # start answering a different question without anybody editing it.
    #
    # Empty means the corpus was not the source -- a bucket object or an
    # upload. It never means "everything": a corpus visual always records
    # what it drew on, even when that is every dataset its author could
    # read.
    datasets = models.JSONField(default=list, blank=True)

    # For CORPUS visuals: the pivot spec (dimensions, measure, filters)
    # that visuals.corpus runs in Postgres. Refreshing re-runs it, so a
    # corpus visual follows the data without re-uploading anything.
    spec = models.JSONField(default=dict, blank=True)

    # The embed stability rule: the pinned snapshot is what embeds serve.
    pinned_snapshot = models.ForeignKey(
        "VisualSnapshot",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # Explicit opt-in for an embed to read live data instead of the pin.
    allow_live = models.BooleanField(
        default=False,
        help_text="Permit ?live=1 on the feed — the embed default stays pinned.",
    )

    # frame-ancestors allowlist for the embed, space-separated origins.
    # `'self'` was the default and it made every embed fail: only
    # data.localnewsimpact.org could frame a page whose entire purpose is to
    # be framed by somebody else's article. The permissive default is what
    # publishing a visual already means. It is safe here because the embed
    # has nothing to hijack -- the data front end carries no session, no
    # sign-in and no write at all (urls_data.py). An author who wants a
    # narrower list still writes one.
    frame_ancestors = models.CharField(
        max_length=500,
        default="*",
        help_text=(
            "CSP frame-ancestors sources, space-separated. "
            "`*` lets any site embed this; name hosts to restrict it."
        ),
    )

    # The project this belongs to, or none. SET_NULL rather than CASCADE:
    # deleting a folder is filing, not destruction, and taking the charts
    # with it would be the worst possible reading of "remove folder".
    folder = models.ForeignKey(
        "Folder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="visuals",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self):
        return f"{self.title} ({self.slug})"

    def clean(self):
        if self.source_kind == BIGQUERY and not self.query.strip():
            raise ValidationError({"query": "A BigQuery visual needs its query."})
        if self.source_kind == GCS and not self.bucket_path.strip():
            raise ValidationError(
                {"bucket_path": "A bucket visual needs its object path."}
            )
        # Inline visuals carry their data as snapshots; nothing to configure.


class Folder(models.Model):
    """A project somebody is working on, named by them.

    Shared rather than personal: a project is a shared thing, and the
    point of naming one is that a colleague can find the work in it. The
    cost is that a folder can hold visuals a given person may not see --
    visibility is per-person and already a union of three rules -- so the
    list shows what each viewer may see and says nothing about the rest.
    An empty-looking folder is the honest rendering of "nothing here for
    you", and a count of hidden things would leak what they are.

    Not derived from the datasets a visual draws on. That grouping is
    free and always correct and answers a different question: what the
    data *is*, rather than what somebody is doing with it. "Drafts for
    the board" is not a dataset.
    """

    name = models.CharField(max_length=120, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class VisualSnapshot(models.Model):
    """One captured run of a visual's data source. Immutable once taken;
    versions count up per visual."""

    visual = models.ForeignKey(
        Visual, on_delete=models.CASCADE, related_name="snapshots"
    )
    version = models.PositiveIntegerField()
    data = models.JSONField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["visual", "version"], name="uq_visual_version"
            )
        ]

    def __str__(self):
        return f"{self.visual.slug} v{self.version}"
