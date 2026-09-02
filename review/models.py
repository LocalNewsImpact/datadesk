"""Import batches and saved export definitions (SCOPE.md §2.4).

Application state, in Datadesk's own database — the crawler corpus is
touched only at apply time, through the audited write path.
"""

from django.conf import settings
from django.db import models


class ImportBatch(models.Model):
    """One uploaded CSV moving through the backpatch protocol:
    upload → column mapping → diff report → explicit apply.

    The batch id is the unit of inspection and revert. Rows are stored
    verbatim at upload so the diff and the apply read the same data.
    """

    UPLOADED = "uploaded"
    MAPPED = "mapped"
    APPLIED = "applied"
    REVERTED = "reverted"
    STATUSES = [(s, s) for s in (UPLOADED, MAPPED, APPLIED, REVERTED)]

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    filename = models.CharField(max_length=255)
    # Which table the batch writes to (review.imports.TARGETS).
    target = models.CharField(max_length=20, default="articles")
    columns = models.JSONField(default=list)
    rows = models.JSONField(default=list)
    key_column = models.CharField(max_length=100, blank=True, default="")
    column_map = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=STATUSES, default=UPLOADED)
    applied_at = models.DateTimeField(null=True, blank=True)
    audit_entry = models.ForeignKey(
        "audit.AuditLogEntry", null=True, blank=True, on_delete=models.PROTECT
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.filename} ({self.status})"


class ExportDefinition(models.Model):
    """A saved export: the grid's filter params plus a column list,
    re-runnable against current data (SCOPE.md §2.4)."""

    name = models.CharField(max_length=200, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    params = models.JSONField(default=dict)
    columns = models.JSONField(default=list)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# The proposal queue lives in its own module; import it here so Django
# discovers the model.
from review.proposals import ChangeProposal  # noqa: E402,F401


class PaywallDismissal(models.Model):
    """Somebody looked at a publisher and said it is not behind a paywall.

    The paywalls page lists publishers the extractor could not read past a
    paywall. `has_paywall` on the record cannot say this by itself: false
    is what all 1,149 records say before anybody has looked, so it means
    "nobody has decided" and "there is no paywall" at once, and a page
    keyed on it would ask about the same publisher for ever.

    So the decision lives here, where review state belongs -- the crawler
    owns the publisher record, and whether somebody has ruled on it is
    this console's business.

    Undone by ticking the paywall box on the record: a publisher whose
    record says it is paywalled is on the page whatever was decided here,
    because the record is the stronger statement.
    """

    source_id = models.TextField(unique=True)
    source_label = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-decided_at"]

    def __str__(self):
        return f"{self.source_label or self.source_id}: no paywall"


class ExtractionDecision(models.Model):
    """What a person decided about a classification the pipeline made.

    Automated triage removes an article from processing by writing a
    status: `not_article`, `obituary`, `weather`, `opinion`, `wire`,
    `paywall`. Each is a claim, and each can be wrong -- 1,517 obituary
    verdicts were made at the detector's own confidence floor of 0.17, on
    a single body phrase, and one of them was a feature about Jim
    Morrison's grave.

    Three decisions, and only one of them writes to the crawler.

    ACCEPT: the classification stands. Nothing is written to the article,
    because its status already excludes it from processing -- enrichment
    selects `labeled` and nothing else. The decision lives here so the
    queue stops asking, the same reason PaywallDismissal exists.

    REJECT: the classification is wrong. The article's status is rewound
    to the stage before the one that erred, and the pipeline carries on
    with the fields it already captured. Re-extraction is not the remedy
    and never was: the article HAS been extracted, and fetching the URL
    again would produce the same result.

    REEXTRACT: the fields themselves are missing, not the verdict wrong.
    Extraction dropped the body on 788 rows, so there is nothing to rewind
    to. The raw HTML in gs://mizzou-news-crawler-raw-html is re-parsed --
    the page as captured, not re-crawled. It has 30-day retention, so this
    is only offered while the archive still holds it.
    """

    ACCEPT = "accept"
    REJECT = "reject"
    REEXTRACT = "reextract"
    DECISIONS = [(d, d) for d in (ACCEPT, REJECT, REEXTRACT)]

    article_id = models.TextField(unique=True)
    article_label = models.TextField(blank=True, default="")
    #: The status the article carried when it was reviewed, so a decision
    #: can be read back against the claim it answered.
    classified_as = models.TextField()
    #: Which stage made the claim: extraction, labeling or enrichment.
    #: It decides where REJECT rewinds to.
    stage = models.TextField(blank=True, default="")
    decision = models.TextField(choices=DECISIONS)
    #: Where REJECT put the status, recorded rather than recomputed: the
    #: rewind map may change, and this says what was actually done.
    rewound_to = models.TextField(blank=True, default="")
    reason = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-decided_at"]

    def __str__(self):
        return f"{self.decision} {self.article_id} ({self.classified_as})"
