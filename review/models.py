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


class ReviewDecision(models.Model):
    """What a person decided about one thing a queue asked about.

    One record for every queue in the console. There were two -- this and
    the decision half of ChangeProposal -- and a third and fourth were
    coming, each with its own table, its own states and its own submit
    path. The question they answer is the same question.

    THE SUBJECT
    -----------
    `subject_type` and `subject_id` say what was decided about: an
    article, a publisher record, later an outlet. Neither alone is enough,
    because ids are only unique within their own table and two queues can
    hold the same id.

    `field` is set where the decision is about one field of the subject --
    a publisher's city, a contradicted owner -- and empty where it is
    about the subject as a whole, which is how the extraction queue
    decides. Field-level is the general case; record-level is the same
    thing with no field named.

    THE QUESTION
    ------------
    `question` is what the decision answers, as a stable key built from
    the claim and the stage that raised it (lnic_contracts.review_note.
    question). Keying on the subject alone was wrong: it would let the
    first decision about an article silence every later question about it,
    and a byline found to be garbage months after the classification was
    settled has to be askable.

    WHAT IT DOES NOT HOLD
    ---------------------
    Anything a queue needs and another does not. A proposal's current and
    proposed values belong to proposals; an article's rewind target
    belongs to extraction. Both are described in `wrote`, which says what
    the verb actually did, in that queue's own words.
    """

    #: Which queue asked. A decision is not portable between queues even
    #: for the same subject: they ask different things.
    queue = models.CharField(max_length=40, db_index=True)

    subject_type = models.CharField(max_length=40, db_index=True)
    subject_id = models.TextField(db_index=True)
    #: Empty for a decision about the subject as a whole.
    field = models.CharField(max_length=60, blank=True, default="")
    #: A human label for the subject, so an audit list reads without
    #: joining across databases -- the subjects are not all in this one.
    subject_label = models.TextField(blank=True, default="")

    #: What was claimed, and which stage claimed it. Together they are the
    #: question.
    claim = models.TextField(blank=True, default="")
    stage = models.CharField(max_length=40, blank=True, default="")
    question = models.TextField()

    #: The verb, as declared by the queue (review/kernel.py).
    verb = models.CharField(max_length=40)
    #: What the person typed, for a verb that takes a value.
    value = models.TextField(blank=True, default="")

    #: What the subject was before, and what the verb made it. Recorded
    #: rather than recomputed: the rules may change, and this says what
    #: was actually done.
    before = models.TextField(blank=True, default="")
    after = models.TextField(blank=True, default="")
    #: What the verb wrote, in the queue's own words. Free-form because
    #: the queues genuinely differ, and forcing a shape here is how the
    #: two records grew apart in the first place.
    wrote = models.JSONField(default=dict, blank=True)

    reason = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-decided_at"]
        indexes = [
            models.Index(fields=["queue", "subject_type", "subject_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["subject_type", "subject_id", "field", "question"],
                name="one_review_decision_per_question",
            )
        ]

    def __str__(self):
        where = f"{self.subject_type}:{self.subject_id}"
        if self.field:
            where = f"{where}.{self.field}"
        return f"{self.verb} {where} ({self.question})"


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

    article_id = models.TextField(db_index=True)
    article_label = models.TextField(blank=True, default="")
    #: WHAT WAS ASKED, as a stable key. This is what a decision answers,
    #: and keying on the article alone was wrong: it would have let the
    #: first decision about an article silence every later question about
    #: it. An article whose byline is later found to be garbage is a new
    #: question, and must be askable even though its classification was
    #: settled months ago.
    #:
    #: Built from the claim and the stage that made it -- "obituary:
    #: extraction" -- so re-flagging the same claim is recognised as the
    #: same question and stays answered.
    #: Blank default for the migration only. `record` always sets it, and
    #: the uniqueness constraint is on (article_id, question), so a blank
    #: cannot collide with a real one on the same article.
    question = models.TextField(default="")
    #: The status the article carried when it was reviewed, so a decision
    #: can be read back against the claim it answered.
    classified_as = models.TextField()
    #: What the article was on before review held it, so ACCEPT can put it
    #: back. Accept means the classification stands, which is a different
    #: thing from leaving the article wherever review parked it.
    status_before = models.TextField(blank=True, default="")
    #: What the disposition required it to become. Recorded rather than
    #: recomputed: the rewind map may change, and this says what was
    #: actually asked for at the time.
    status_after = models.TextField(blank=True, default="")
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
        constraints = [
            models.UniqueConstraint(
                fields=["article_id", "question"],
                name="one_decision_per_question",
            )
        ]

    def __str__(self):
        return f"{self.decision} {self.article_id} ({self.question})"


class RepeatedBody(models.Model):
    """A publisher producing many articles with byte-identical body lengths.

    A parser that meets a page shape it does not handle returns the same
    thing every time -- a comment policy, a subscriber wall, a list of
    counties -- and the tell is that the length repeats exactly. On
    2026-09-04: 486 articles from newspressnow.com at exactly 228
    characters, every one of them the site's comment policy, and 472 of
    those were classified `wire`. A failed capture recorded as
    syndication, 486 times, with nobody looking.

    This is the half nobody has reported. review/extraction_problems.py
    counts what reviewers found; this counts what the corpus shows on its
    own.

    Computed on a schedule rather than per request: the query groups
    164,000 articles by host and length and takes about 32 seconds.

    IS IT STILL HAPPENING
    ---------------------
    `latest_article` is the point. A pattern whose newest article is from
    March is one somebody already fixed; one that grew this week is not,
    and a list that cannot tell them apart is a list nobody reads twice.
    """

    host = models.CharField(max_length=255, db_index=True)
    #: The length every one of these bodies has, exactly.
    length = models.PositiveIntegerField()
    articles = models.PositiveIntegerField()
    #: Enough of the text to recognise it. A comment policy and a
    #: subscriber wall are both "short and repeated"; only the words say
    #: which, and which parser rule is missing.
    sample = models.TextField(blank=True, default="")
    #: What the pipeline decided these were, as {status: count}. The
    #: interesting case is a failed capture confidently labelled
    #: something else.
    statuses = models.JSONField(default=dict, blank=True)
    #: The newest article in the pattern: whether this is still going on.
    latest_article = models.DateTimeField(null=True, blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-articles"]
        constraints = [
            models.UniqueConstraint(
                fields=["host", "length"], name="one_row_per_host_and_length"
            )
        ]

    def __str__(self):
        return f"{self.host} × {self.length} chars ({self.articles})"


class WorklistCount(models.Model):
    """How much is waiting in one queue, in one directory.

    A row per (directory, queue), refreshed on a schedule, so the to-do is
    ONE select instead of a query per queue per directory across two
    databases.

    Why it is a table and not a cache

    The counts are joins over the crawler's largest tables. One of them --
    flagged articles in a single directory -- takes 11.2 seconds against
    production, with every join column already indexed: 164,570 articles
    against 262,137 candidate links is fan-out, not a missing index. Nine
    such queries is a landing page nobody waits for.

    A cache does not fix that. It moves the wait to whoever arrives after
    it expires, which is the same person, less often, more confusingly. A
    table refreshed off the request path means no reader ever pays for it.

    Stale on purpose

    `updated_at` is shown rather than hidden. A number somebody plans a
    morning around should say how old it is, and a refresh that has stopped
    running is then visible as a number that stops moving -- rather than as
    a queue that quietly looks empty.
    """

    dataset_slug = models.TextField()
    #: Which queue: proposals, extraction, paywalls. Not a choices field --
    #: a queue added later should not need a migration to be counted.
    queue = models.TextField()
    count = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["dataset_slug", "queue"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_slug", "queue"], name="one_count_per_queue_per_dataset"
            )
        ]
        indexes = [models.Index(fields=["dataset_slug"])]

    def __str__(self):
        return f"{self.dataset_slug}/{self.queue}: {self.count}"
