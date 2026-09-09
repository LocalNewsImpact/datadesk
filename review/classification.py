"""Cohorts, samples and assignments for the classification queue.

The queue collects human CIN labels to measure the production model
against and to retrain it. Three things have to hold for those labels to
be worth anything, and each is a table here.

**Every record is seen by the same number of coders.** Serving whatever
has fewest answers, first come first served, gives no guarantee: a keen
reviewer answers a thousand records once each and nothing reaches three.
So records are assigned.

**A coder sees the records they were given and no others.** A coder who
can choose what to label produces a sample somebody chose, which is not
a sample. `classifier` holds `classify` and not `read` for this reason
(accounts/privileges.py), and the assignment is the whole of their
access.

**How a record was drawn is recorded with it.** A rate measured over
rows that were not equally likely to be drawn is not a rate. The
inclusion probability travels on the sample row so an oversampled draw
can still be weighted back to corpus rates.

Nothing here is written to the crawler. `article_labels` is the model's
record of its own output; a human label written there would corrupt the
thing being measured.

See docs/CLASSIFICATION_REVIEW_QUEUE.md.
"""

from django.conf import settings
from django.db import models


class ClassificationCohort(models.Model):
    """A batch of records drawn together and worked as a unit.

    A cohort is also a **scope**. A classifier is granted a cohort
    rather than a dataset — `Grant(user, DATADESK, scope="cohort-3",
    role="classifier")` — and `scopes_for(user, CLASSIFY)` reads it back.
    `Grant.scope` is a slug, so this needs no new access machinery.

    It is not a dataset. Cohorts are Datadesk objects and not rows in
    the crawler's `datasets` table, so `datasets_for()` cannot surface
    one in a picker: a cohort is an overlay on the corpus, and the
    articles in it still belong to Mizzou or VT everywhere else.

    Cohorts are what makes "how are we doing" answerable. Without them
    there is one undifferentiated pile, and the questions people
    actually ask — has last month's batch finished, did agreement
    improve after the guidance was rewritten, is this week slower than
    last — have nowhere to attach.
    """

    number = models.PositiveIntegerField(unique=True)
    #: What a Grant scopes to. Derived from the number rather than typed,
    #: so a grant cannot name a cohort that does not exist.
    slug = models.SlugField(max_length=40, unique=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    #: Set when it stops taking work. Null while it is being worked.
    closed_at = models.DateTimeField(null=True, blank=True)
    #: How many coders must dispose of each record before it counts.
    target_coders = models.PositiveSmallIntegerField(default=3)
    #: An assignment older than this is reassigned. Without it, one
    #: absent coder leaves records stuck below target forever — the
    #: predictable way this design fails, and it fails quietly.
    stale_after_days = models.PositiveSmallIntegerField(default=14)
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-number"]

    def __str__(self):
        return f"cohort {self.number}"

    @property
    def is_open(self):
        return self.closed_at is None

    @staticmethod
    def slug_for(number):
        return f"cohort-{number}"


class ClassificationSample(models.Model):
    """One article drawn into one cohort, and how it was drawn.

    Membership is permanent: an article drawn into cohort 3 stays in
    cohort 3, so a rate computed over that cohort does not move when a
    later one is drawn.
    """

    #: Which draw put it in front of somebody. `doubtful` and `overruled`
    #: follow from the row; membership of the random stratum follows from
    #: nothing about it, which is what makes it random — so it cannot be
    #: recomputed later and has to be stored.
    CONFIDENT = "confident"
    UNCERTAIN = "uncertain"
    RANDOM = "random"
    UNLABELLED = "unlabelled"
    STRATA = [(s, s) for s in (CONFIDENT, UNCERTAIN, RANDOM, UNLABELLED)]

    cohort = models.ForeignKey(
        ClassificationCohort, on_delete=models.CASCADE, related_name="samples"
    )
    #: The crawler's article id. Not a ForeignKey: `articles` lives in
    #: another database and this one may not join to it.
    article_id = models.CharField(max_length=64, db_index=True)
    stratum = models.CharField(max_length=20, choices=STRATA)
    #: The chance a row in this stratum had of being drawn. 1.0 where the
    #: stratum was taken whole. Without it an oversampled draw cannot be
    #: weighted back to corpus rates, and the same rows can serve
    #: training or evaluation but not both.
    inclusion_probability = models.FloatField(default=1.0)
    #: Recorded at draw time so a per-dataset rate does not depend on a
    #: join to a corpus that may have moved.
    dataset_id = models.CharField(max_length=64, blank=True, default="")
    #: What the model said when the row was drawn, kept so agreement can
    #: be computed without re-reading a table that keeps changing.
    model_label = models.CharField(max_length=64, blank=True, default="")
    model_confidence = models.FloatField(null=True, blank=True)
    drawn_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "article_id"], name="one_sample_per_cohort_article"
            )
        ]
        indexes = [models.Index(fields=["cohort", "stratum"])]

    def __str__(self):
        return f"{self.article_id} in {self.cohort}"


class ClassificationAssignment(models.Model):
    """One coder's claim on one record in one cohort.

    Drawing a cohort creates `target_coders` assignments per record,
    spread evenly across the available coders, so every record gets the
    same number of evaluators and every coder gets roughly the same
    amount of work.

    A coder's queue is their outstanding assignments, oldest first. They
    are never shown a record they were not assigned, and never the same
    record twice.
    """

    cohort = models.ForeignKey(
        ClassificationCohort, on_delete=models.CASCADE, related_name="assignments"
    )
    article_id = models.CharField(max_length=64, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    #: Set when a decision lands. Null while outstanding. Recorded from
    #: the start even before anything reads it: the alert that says a
    #: cohort will not finish on time needs throughput history, and
    #: history cannot be collected retrospectively.
    completed_at = models.DateTimeField(null=True, blank=True)
    #: Why this assignment exists, where it replaced a stale one. Kept
    #: rather than overwritten: a coder whose assignments are routinely
    #: reassigned is a fact worth knowing, and so is a cohort that needed
    #: a lot of it.
    reassigned_from = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "article_id", "assigned_to"],
                name="one_assignment_per_coder_per_article",
            )
        ]
        indexes = [
            models.Index(fields=["assigned_to", "completed_at"]),
            models.Index(fields=["cohort", "completed_at"]),
        ]

    def __str__(self):
        return f"{self.article_id} -> {self.assigned_to}"


class ClassificationDecision(models.Model):
    """What one coder said about one article.

    Not `ReviewDecision`. That is unique on (subject_type, subject_id,
    field, question) — one decision per article — and this queue needs
    three coders to answer the same record independently.

    A primary label, and a secondary only where the coder had
    significant confidence in it. The production model was trained on
    weighted values derived from exactly this pair (primary 1.0,
    secondary 0.5), and `article_labels` stores the model's own answer
    the same way.
    """

    #: Why a record could not be classified. The first four are what
    #: coders actually reached for in the historical cohorts, where they
    #: were folded into the primary dropdown; `NOT LOCAL` was the most
    #: used and is missing from most proposals of this list.
    NOT_LOCAL = "not_local"
    NO_CATEGORY = "no_appropriate_category"
    TOO_SHORT = "too_short"
    TECHNICAL_ERROR = "technical_error"
    PAYWALL_STUB = "paywall_stub"
    GARBAGE_TEXT = "garbage_text"
    REJECTIONS = [
        (NOT_LOCAL, "Not local — wire or other non-local content"),
        (NO_CATEGORY, "No appropriate category"),
        (TOO_SHORT, "Too short to evaluate"),
        (TECHNICAL_ERROR, "Technical error — text missing or mismatched"),
        (PAYWALL_STUB, "Paywall stub"),
        (GARBAGE_TEXT, "Garbage text"),
    ]

    cohort = models.ForeignKey(
        ClassificationCohort, on_delete=models.PROTECT, related_name="decisions"
    )
    article_id = models.CharField(max_length=64, db_index=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    #: One of the ten CIN categories, or empty where the record was
    #: rejected. Not a choices field: the vocabulary is the crawler's
    #: (`CRITICAL_INFORMATION_NEEDS_LABELS`) and restating it here would
    #: be a second place for it to drift.
    primary_label = models.CharField(max_length=64, blank=True, default="")
    secondary_label = models.CharField(max_length=64, blank=True, default="")
    reject_reason = models.CharField(
        max_length=32, choices=REJECTIONS, blank=True, default=""
    )
    #: Copied from the sample so a decision can be weighted without
    #: joining back to a draw that may have been superseded.
    stratum = models.CharField(max_length=20, blank=True, default="")
    inclusion_probability = models.FloatField(default=1.0)
    dataset_id = models.CharField(max_length=64, blank=True, default="")
    #: How long the coder spent, in seconds. The only defence against a
    #: coder who is clicking through, short of the seeded check records,
    #: and it costs nothing to record.
    seconds_spent = models.PositiveIntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-decided_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["article_id", "decided_by"],
                name="one_classification_per_coder_per_article",
            )
        ]
        indexes = [
            models.Index(fields=["cohort", "article_id"]),
            models.Index(fields=["primary_label"]),
        ]

    def __str__(self):
        said = self.primary_label or self.reject_reason or "nothing"
        return f"{self.decided_by} said {said} of {self.article_id}"

    @property
    def is_rejection(self):
        return bool(self.reject_reason)
