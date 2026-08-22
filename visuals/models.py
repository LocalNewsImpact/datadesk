"""The visuals registry (SCOPE.md §2.7 v1).

A Visual is authored as code — a renderer template in the repo plus a
data source (a named BigQuery query or a bucket object) — and registered
and published through the admin. Publishing pins a data snapshot; embeds
serve the pinned version by default with an explicit opt-in to live data
(the embed stability rule: a published report must not change under its
readers).
"""

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
    frame_ancestors = models.CharField(
        max_length=500,
        default="'self'",
        help_text="CSP frame-ancestors sources, space-separated.",
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
