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

import random

from django.conf import settings
from django.db import models
from lnic_contracts import cin_labels

from accounts.privileges import CLASSIFIER
from review import cin_metrics

#: The ten Critical Information Needs categories, in the order the
#: codebook introduces them -- which is the order a person reads them in
#: and the right one for a dropdown.
#:
#: From lnic-contracts, not restated. I wrote them out here first and
#: used a DIFFERENT order from the crawler's without noticing, which is
#: exactly the drift the contract exists to prevent: the crawler's order
#: is its model's class ids, so the two are not interchangeable and a
#: local copy invites treating them as though they were.
#:
#: `CODEBOOK_ORDER` is the display order. `LABELS` is the model's, and
#: nothing here should use it -- a coder reads a list, they do not read
#: class ids.
CIN_LABELS = cin_labels.CODEBOOK_ORDER

#: What a coder is shown about each category, from `docs/CODEBOOK.md`.
#:
#: The codebook is the specification the model is built on, and it was a
#: PDF attachment until this repository transcribed it. A coder working
#: the queue cannot read a PDF attachment, so the parts they need while
#: deciding travel here: what the category covers, and the action test
#: line -- what a reader could DO having read the story, which is the
#: instrument the codebook gives for choosing between categories.
#:
#: Compressed from the full definitions deliberately. The whole text is
#: 90 lines and a panel nobody finishes reading is a panel nobody reads.
#: The full definitions are one link away, in the same document.
#:
#: `test_every_category_has_guidance` holds this to CIN_LABELS, so a
#: category can never appear in the menu with nothing said about it. It
#: caught two on the first run.
#:
#: The keys are cased as the vocabulary cases them -- "Civic
#: information" and "Political life" beside "Civic Life". That is not a
#: typo to tidy: these strings are the model's class names, and the
#: codebook's own worked-examples table spells two of them differently
#: from the vocabulary the classifier was trained on.
CODING_GUIDE = {
    "Emergencies and Public Safety": (
        "Dangerous weather, biohazards, terrorism and amber alerts, "
        "policing and neighborhood public safety.",
        "know where to seek shelter from a tornado",
    ),
    "Health": (
        "Local health and healthcare: availability, quality and cost of "
        "care, public health programs, disease spread and vaccination.",
        "learn where to get a flu vaccine",
    ),
    "Education": (
        "Quality and administration of local schools, school "
        "alternatives and charters, enrichment and afterschool, adult "
        "education and job training, local higher education.",
        "decide which school to send their children to",
    ),
    "Transportation Systems": (
        "Mass transit at neighborhood, city and regional level; traffic "
        "and road conditions including closings; public debate on roads "
        "and transit.",
        "plan their bus route across town",
    ),
    "Environment and Planning": (
        "Water and air quality, toxic hazards and brownfields, natural "
        "resource development, sustainability, access to recreation and "
        "habitat restoration.",
        "plant sustainable native flora in their front yard",
    ),
    "Economic Development": (
        "Employment and job openings, training, retraining and "
        "apprenticeship, small business startup assistance and capital, "
        "major development initiatives.",
        "find a new job opportunity",
    ),
    "Civic information": (
        "Major civic institutions, nonprofits and associations -- their "
        "services, accessibility and opportunities to take part. "
        "Libraries and community information services.",
        "volunteer at the library",
    ),
    "Civic Life": (
        "Things to do and places to go: cultural arts, recreation, "
        "community social services and programs, religious institutions, "
        "and profiles of the people behind them.",
        "pick a restaurant for lunch",
    ),
    "Political life": (
        "Candidates and elections at every local level, neighborhood "
        "councils and school boards, public meetings and their outcomes, "
        "voter registration and absentee rules.",
        "select their candidate in the next city election",
    ),
    "Sports": (
        "Game stories, athlete profiles, and coverage of organizations "
        "and facilities tied to athletic competition.",
        "check the box score from last night's game",
    ),
}


def guidance():
    """The guide in menu order, as rows a template can walk.

    Ordered by CIN_LABELS rather than by the dict, so the panel and the
    menu can never disagree about what comes first.
    """
    return [
        {
            "label": label,
            "covers": CODING_GUIDE[label][0],
            "reader_could": CODING_GUIDE[label][1],
        }
        for label in CIN_LABELS
        if label in CODING_GUIDE
    ]


#: What the instructions panel says before anybody edits it, from
#: `docs/CODEBOOK.md`. A default rather than fixed text: the wording is
#: expected to change, and changing it is how agreement gets tested.
DEFAULT_INSTRUCTIONS = """\
Consider what specific understanding or action - on balance - might be
undertaken as a result of the content.

That is the test the codebook gives, and it is the one to reach for when
two categories both look plausible: ask what the reader could go and do,
not what the story is about.

A primary category is required. Where the story genuinely carries a
second, set a secondary - but only where you have significant confidence
it is present. A secondary added on a hunch is worth less than none:
these labels become training weights, and a doubtful second category is
scored at half the weight of the first rather than discarded.

Reach for a rejection rather than forcing a category. "Not local" was
the most used of these in the original coding, and a wire story given a
CIN category teaches the model that wire is local.

Civic information and Civic Life are the pair coders disagreed on most.
Civic information is the institution - a library, a nonprofit, a
community service, and how to reach it or take part. Civic Life is the
going-out: things to do, places to go, arts and recreation, and the
people behind them. Volunteering at the library is Civic information;
the exhibition in its gallery is Civic Life.
"""


class CodebookSettings(models.Model):
    """The coding instructions, editable without a deploy.

    The codebook is the specification the classifier is built on, and it
    spent its life as a PDF attachment. Transcribing it to
    `docs/CODEBOOK.md` made it readable; it did not make it changeable
    by the people who own it, because a coder-facing wording change
    still meant a pull request and a deploy.

    That matters more here than in most places. The definitions are
    known to be imperfect -- agreement across the original cohorts was
    64.6% exact, and Civic information behaves as a catch-all -- so the
    guidance WILL be rewritten, and the whole point of rewriting it is
    to measure whether agreement moves afterwards. `updated_at` is what
    lets a later analysis cut agreement by which wording was live when
    the decision was made.

    One row. `load()` is the only way in, and it seeds from the codebook
    rather than starting blank: an empty instructions panel on a fresh
    database would be a queue shipped without its specification.
    """

    #: Shown in the classifier's instructions panel, above the category
    #: table. Markdown-free: it is rendered as paragraphs, because a
    #: text box that silently accepts HTML from an admin is a stored
    #: XSS waiting for the first person who pastes from Word.
    instructions = models.TextField(blank=True, default="")
    #: The draft of everything else a version carries. Empty means "as
    #: V1": the guidance in `CODING_GUIDE`, labels shown as stored, the
    #: six original rejections.
    guidance = models.JSONField(default=dict, blank=True)
    display_names = models.JSONField(default=dict, blank=True)
    rejections = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name_plural = "codebook settings"

    def draft_guidance(self):
        """label -> [covers, reader_could], complete for every label."""
        return {
            label: list(self.guidance.get(label) or CODING_GUIDE[label])
            for label in CIN_LABELS
        }

    def draft_rejections(self):
        return list(self.rejections) or list(V1_REJECTIONS)

    def freeze(self, name, user=None):
        """Copy the draft into a new, immutable `CodebookVersion`.

        The draft is what an admin edits; a version is what a cohort is
        coded under. Editing the draft changes nothing a coder sees
        until it is frozen and a cohort is opened under the result.
        """
        return CodebookVersion.objects.create(
            name=name,
            instructions=self.instructions,
            guidance=self.draft_guidance(),
            display_names={k: v for k, v in self.display_names.items() if v and v != k},
            rejections=self.draft_rejections(),
            frozen_by=user,
        )

    def __str__(self):
        return f"codebook settings, updated {self.updated_at:%Y-%m-%d}"

    @classmethod
    def load(cls):
        """The single row, seeded from the codebook on first use."""
        row = cls.objects.first()
        if row is None:
            row = cls.objects.create(instructions=DEFAULT_INSTRUCTIONS)
        return row

    def paragraphs(self):
        """The instructions as paragraphs, split on blank lines.

        Rendered as text rather than markup for the reason the field
        comment gives.
        """
        blocks = [b.strip() for b in self.instructions.split("\n\n")]
        return [" ".join(b.split()) for b in blocks if b.strip()]


#: The rejections the original codebook and the first datadesk cohorts
#: offered. V2 adds three; a version lists the ones it offers, so a V1
#: cohort keeps exactly these.
V1_REJECTIONS = (
    "not_local",
    "no_appropriate_category",
    "too_short",
    "technical_error",
    "paywall_stub",
    "garbage_text",
)


class CodebookVersion(models.Model):
    """A frozen codebook: what coders in a cohort were actually shown.

    Agreement is only comparable between answers given under the same
    definitions. `CodebookSettings.updated_at` could say which wording
    was live when a decision was made, but only by inference, and it
    could not say what the category guidance or the menu was. A version
    is the whole of what a coder saw -- instructions, per-category
    guidance, the names in the menu, the rejections on offer -- and a
    cohort names the one it was coded under.

    Immutable. A change is a new version, because a version edited after
    answers were given under it describes definitions nobody coded to.

    `display_names` maps a stored label to the name shown. The stored
    label is the model's class name (`lnic_contracts.cin_labels`), so
    V2's rename of Civic Life to Community Life is a display name here
    and not a change to what is stored: renaming the stored value would
    misalign every existing label and the model trained on them.
    """

    name = models.CharField(max_length=20, unique=True)
    instructions = models.TextField(blank=True, default="")
    guidance = models.JSONField(default=dict)
    display_names = models.JSONField(default=dict, blank=True)
    rejections = models.JSONField(default=list)
    frozen_at = models.DateTimeField(auto_now_add=True)
    frozen_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-frozen_at", "-id"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError(
                f"codebook version {self.name} is frozen; freeze a new one"
            )
        super().save(*args, **kwargs)

    @classmethod
    def v1(cls):
        """The codebook every cohort before versions was coded under."""
        row = cls.objects.filter(name="V1").first()
        if row is None:
            row = cls.objects.create(
                name="V1",
                instructions=DEFAULT_INSTRUCTIONS,
                guidance={label: list(CODING_GUIDE[label]) for label in CIN_LABELS},
                display_names={},
                rejections=list(V1_REJECTIONS),
            )
        return row

    @classmethod
    def current(cls):
        """The most recently frozen version: what a new cohort is opened
        under unless told otherwise."""
        return cls.objects.first() or cls.v1()

    def shown(self, label):
        """The name a coder sees for a stored label."""
        return self.display_names.get(label) or label

    def label_choices(self):
        """(stored, shown) in menu order."""
        return [(label, self.shown(label)) for label in CIN_LABELS]

    def guidance_rows(self):
        return [
            {
                "label": self.shown(label),
                "covers": self.guidance[label][0],
                "reader_could": self.guidance[label][1],
            }
            for label in CIN_LABELS
            if label in self.guidance
        ]

    def rejection_choices(self):
        return [
            (value, label)
            for value, label in ClassificationDecision.REJECTIONS
            if value in self.rejections
        ]

    def paragraphs(self):
        blocks = [b.strip() for b in self.instructions.split("\n\n")]
        return [" ".join(b.split()) for b in blocks if b.strip()]


class Round(models.Model):
    """What a run of coding is for, and when it is finished.

    A cohort is a batch of work and carries no reason. Practice coding,
    a test of new definitions and the collection of training data were
    indistinguishable, so nothing could say what counted toward
    agreement or when the work was done. A round owns the cohorts opened
    for one purpose, names the codebook they are coded under, and says
    how many coders read each story. See docs/CIN_ROUNDS.md and
    docs/CIN_V2_DATADESK.md (WP2).

    Three kinds, because they finish differently and count differently:

    practice    every coder codes every story, the lead reviews the
                disagreements with them, and none of it is counted --
                not in agreement, not in training.
    comparison  the pilot: do coders agree under this codebook?
    collection  training data, finished when every category has enough.
    """

    PRACTICE = "practice"
    COMPARISON = "comparison"
    COLLECTION = "collection"
    KINDS = [
        (PRACTICE, "Practice — not counted"),
        (COMPARISON, "Pilot — do coders agree?"),
        (COLLECTION, "Training — collect labels"),
    ]

    name = models.CharField(max_length=60, unique=True)
    kind = models.CharField(max_length=12, choices=KINDS)
    codebook_version = models.ForeignKey(
        "CodebookVersion", on_delete=models.PROTECT, related_name="rounds"
    )
    question = models.TextField(blank=True, default="")
    #: Coders per story. Ignored by a practice round, where every coder
    #: codes every story.
    base_coders = models.PositiveSmallIntegerField(default=3)
    #: A third coder only where the first two differ. Recorded here;
    #: acted on by the assignment step that adds it (WP5).
    third_on_split = models.BooleanField(default=False)
    #: How stories are chosen (WP4) and when the round is finished. JSON
    #: because the rules differ by kind; `per_category` is the floor a
    #: collection round must reach in every category.
    draw_rule = models.JSONField(default=dict, blank=True)
    finish_rule = models.JSONField(default=dict, blank=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    #: A practice round ends with the lead going through every
    #: disagreement with the coders. That the meeting was held, and what
    #: it changed in the definitions, is part of the record.
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    review_notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-opened_at", "-id"]

    def __str__(self):
        return self.name

    @property
    def is_practice(self):
        return self.kind == self.PRACTICE


class CoderStanding(models.Model):
    """Whether a coder may be given counted work under a codebook.

    Append-only: the latest row for a coder and a codebook version is
    their standing, and the earlier rows are how they got there -- who
    admitted them and when, who paused them and why.

    A coder is ADMITTED after finishing the practice and its review.
    Until then they are given practice stories and nothing else, because
    answers from somebody who has not been through the definitions are
    measuring the onboarding and not the codebook.

    PAUSED takes no new work and gives back what is outstanding.
    REMOVED does the same and also withdraws the answers already given:
    they leave every agreement figure and the training export, and the
    stories go back to be coded by someone else.
    """

    ADMITTED = "admitted"
    PAUSED = "paused"
    REMOVED = "removed"
    STATUSES = [(ADMITTED, "Admitted"), (PAUSED, "Paused"), (REMOVED, "Removed")]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    codebook_version = models.ForeignKey(
        "CodebookVersion", on_delete=models.PROTECT, related_name="standings"
    )
    status = models.CharField(max_length=10, choices=STATUSES)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-changed_at", "-id"]
        indexes = [models.Index(fields=["codebook_version", "user"])]

    def __str__(self):
        return f"{self.user} {self.status} under {self.codebook_version}"


class GateResult(models.Model):
    """A round measured against its finish line, as it stood then.

    Whether new definitions passed their pilot is the decision the whole
    programme turns on, and it must be possible to say afterwards what
    the figures were when it was taken. So an evaluation is written
    down -- every condition with its value, its interval, its threshold
    and its outcome -- and never changed. Evaluating again adds a row.

    Three outcomes, because "not yet" is not "no": a round that has not
    been coded far enough to judge is NOT_ENOUGH, and is never shown as
    a failure.
    """

    PASS = "pass"
    FAIL = "fail"
    NOT_ENOUGH = "not_enough"
    OUTCOMES = [(PASS, "Pass"), (FAIL, "Fail"), (NOT_ENOUGH, "Not enough data")]

    round = models.ForeignKey(Round, on_delete=models.PROTECT, related_name="gates")
    evaluated_at = models.DateTimeField(auto_now_add=True)
    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    outcome = models.CharField(max_length=12, choices=OUTCOMES)
    #: One entry per condition: name, population, value, low, high, n,
    #: threshold, the V1 figure on the same stories, outcome.
    conditions = models.JSONField(default=list)
    #: What was measured beside the conditions: the change from V1,
    #: alpha, the share of stories with a label, stories left out.
    measures = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-evaluated_at", "-id"]

    def __str__(self):
        return f"{self.round}: {self.outcome}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("a gate result is a record; evaluate again instead")
        super().save(*args, **kwargs)


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
    #: The codebook this cohort is coded under. Null only on rows from
    #: before versions existed, which `codebook` reads as V1.
    codebook_version = models.ForeignKey(
        CodebookVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cohorts",
    )

    class Meta:
        ordering = ["-number"]

    #: The round this batch belongs to. Null on cohorts opened before
    #: rounds existed, which behave as they always did.
    round = models.ForeignKey(
        Round, null=True, blank=True, on_delete=models.PROTECT, related_name="cohorts"
    )

    @property
    def codebook(self):
        return self.codebook_version or CodebookVersion.v1()

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
    #: Draws made by a round's own rule rather than the four above.
    #: `civic` and `other` are the two groups of a pilot drawn from the
    #: V1 stories; `category` is a training draw made for one category by
    #: the model's label.
    CIVIC = "civic"
    OTHER = "other"
    CATEGORY = "category"
    STRATA = [
        (s, s)
        for s in (CONFIDENT, UNCERTAIN, RANDOM, UNLABELLED, CIVIC, OTHER, CATEGORY)
    ]

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
    #: Held back to test the retrained model, and so never trained on.
    #: Decided when the story is drawn, before anybody has coded it: a
    #: test set chosen after the labels are known is chosen by them.
    holdout = models.BooleanField(default=False)
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


def queue_position():
    """Where a new assignment falls in its coder's queue: anywhere."""
    return random.random()


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
    #: A check story: one with a known answer, hidden among a coder's
    #: real work to see whether they are reading. Its answer is scored
    #: against the known one and counted nowhere else.
    is_check = models.BooleanField(default=False)
    #: Where it falls in the coder's queue. Random, so that work made in
    #: one step -- a batch, a set of check stories -- is not met in the
    #: order it was made, which would announce what it is.
    position = models.FloatField(default=queue_position)
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
    #: Added by codebook V2. The least-confident Civic Life and Civic
    #: information labels in the original coding were mostly these three
    #: kinds of story: with no way to say so, a coder reaches for the
    #: broadest-sounding category.
    OBITUARY = "obituary"
    ADVERTORIAL = "advertorial"
    SERVICE_COPY = "service_copy"
    REJECTIONS = [
        (NOT_LOCAL, "Not local — wire or other non-local content"),
        (NO_CATEGORY, "No appropriate category"),
        (TOO_SHORT, "Too short to evaluate"),
        (TECHNICAL_ERROR, "Technical error — text missing or mismatched"),
        (PAYWALL_STUB, "Paywall stub"),
        (GARBAGE_TEXT, "Garbage text"),
        (OBITUARY, "Obituary or death notice"),
        (ADVERTORIAL, "Advertorial — sponsored or promotional content"),
        (SERVICE_COPY, "Service or lifestyle copy — not about the community"),
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
    #: Copied from the cohort, as `stratum` is from the sample: the
    #: definitions an answer was given under travel with the answer.
    codebook_version = models.ForeignKey(
        CodebookVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    #: Copied from the sample so a decision can be weighted without
    #: joining back to a draw that may have been superseded.
    stratum = models.CharField(max_length=20, blank=True, default="")
    inclusion_probability = models.FloatField(default=1.0)
    dataset_id = models.CharField(max_length=64, blank=True, default="")
    #: Given on a check story. Left out of every agreement figure and of
    #: the training export: it measures the coder, not the story.
    is_check = models.BooleanField(default=False)
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


# ------------------------------------------------------------- the reports
#
# What the CIN admin section answers. Kept here rather than in the view
# because the agreement arithmetic is the substance -- a view should not
# be where "do two coders agree" is decided.


class CheckStory(models.Model):
    """A story with a known answer, for checking that a coder is reading.

    Agreement between coders cannot catch a coder who is not reading:
    two who both click the first option agree perfectly. A check story
    can, and it costs about one story in fifty. The known answer is the
    category every coder in a pilot chose under the same codebook, so it
    is the codebook's answer and nobody's opinion.
    """

    codebook_version = models.ForeignKey(
        "CodebookVersion", on_delete=models.PROTECT, related_name="check_stories"
    )
    article_id = models.CharField(max_length=64)
    answer = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["codebook_version", "article_id"],
                name="one_check_answer_per_story_per_codebook",
            )
        ]

    def __str__(self):
        return f"{self.article_id}: {self.answer}"


class CheckResult(models.Model):
    """A coder's check stories, judged once.

    At 40 check stories a coder is paused if they missed more than a
    coder working normally would by chance 1 time in 100, where normal
    is the other coders' combined miss rate on check stories. Judged
    once and written down with the figures it was judged on: a rule
    looked at after every story pauses careful coders by accident.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    round = models.ForeignKey(
        "Round", on_delete=models.PROTECT, related_name="check_results"
    )
    stories = models.PositiveIntegerField()
    misses = models.PositiveIntegerField()
    normal_miss_rate = models.FloatField()
    threshold = models.PositiveIntegerField()
    paused = models.BooleanField(default=False)
    judged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "round"], name="one_check_result_per_coder_per_round"
            )
        ]


class ClassificationStory(models.Model):
    """A story to code that is not in the crawler.

    The queue read crawler articles and nothing else. The stories coded
    in 2023 under the first codebook are not there: the crawler's
    earliest extraction is September 2025, and none of them matches by
    URL or id. Re-crawling them would change the text, hit paywalls and
    push 2023 stories into the crawler's enrichment and export. They are
    loaded here instead, with the headline and text the V1 coders read,
    so that a V2 answer is an answer about the same thing.

    The id carries a namespace (`v1:500`) so it can sit in the same
    `article_id` columns as a crawler UUID and never collide with one.
    """

    V1 = "v1-2023"

    #: What the V1 coding made of the story, by the thesis rule.
    LABEL_09 = "label_09"
    LABEL_07 = "label_07"
    REJECTED = "rejected"
    NO_LABEL = "none"
    OUTCOMES = [
        (LABEL_09, "Two coders chose the same primary"),
        (LABEL_07, "One coder's primary is another's secondary"),
        (REJECTED, "Two coders rejected it"),
        (NO_LABEL, "No agreed label"),
    ]

    id = models.CharField(max_length=64, primary_key=True)
    origin = models.CharField(max_length=20, default=V1)
    headline = models.TextField(blank=True, default="")
    body = models.TextField(blank=True, default="")
    url = models.TextField(blank=True, default="")
    host = models.CharField(max_length=200, blank=True, default="")
    source_hash = models.CharField(max_length=64, blank=True, default="")
    v1_outcome = models.CharField(
        max_length=12, choices=OUTCOMES, blank=True, default=""
    )
    v1_label = models.CharField(max_length=64, blank=True, default="")
    #: Any V1 coder put it in Civic information or Civic Life, primary
    #: or secondary: the stories the V2 definitions are tested on.
    civic = models.BooleanField(default=False)

    def __str__(self):
        return self.id

    # The two names the queue reads from a crawler article.
    @property
    def title(self):
        return self.headline

    @property
    def text(self):
        return self.body


class V1Decision(models.Model):
    """What one V1 coder said about one story.

    Not a `ClassificationDecision`: the V1 coders are not users here,
    and their answers are the baseline V2 is measured against rather
    than answers given in this queue.
    """

    story = models.ForeignKey(
        ClassificationStory, on_delete=models.CASCADE, related_name="v1_decisions"
    )
    group = models.CharField(max_length=1)
    primary_label = models.CharField(max_length=64, blank=True, default="")
    secondary_label = models.CharField(max_length=64, blank=True, default="")
    reject_reason = models.CharField(max_length=32, blank=True, default="")
    also_present = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["story", "group"], name="one_v1_decision_per_group_per_story"
            )
        ]

    def __str__(self):
        return f"{self.story_id} group {self.group}"


def stories_for(article_ids):
    """id -> something with `.title` and `.text`, wherever the story is.

    A namespaced id (`v1:500`) is a `ClassificationStory`; anything else
    is a crawler article. One lookup, so the queue and the practice
    review cannot disagree about where a story lives.
    """
    from explorer.models import Article

    ids = list(article_ids)
    ours = [i for i in ids if ":" in i]
    theirs = [i for i in ids if ":" not in i]
    found = {}
    if ours:
        found.update(ClassificationStory.objects.in_bulk(ours))
    if theirs:
        found.update(Article.objects.using("crawler").in_bulk(theirs))
    return found


def standings(version):
    """user id -> current standing under a codebook version."""
    latest = {}
    for row in CoderStanding.objects.filter(codebook_version=version).order_by(
        "changed_at", "id"
    ):
        latest[row.user_id] = row.status
    return latest


def set_standing(user, version, status, *, by=None, note=""):
    """Record a coder's standing, and do what it implies.

    Pausing or removing gives back the coder's outstanding work in every
    counted cohort under this codebook, and hands it to somebody else:
    a cohort otherwise waits forever on a coder who will not be back.
    Removing also withdraws their answers, so those stories are short a
    coder again and are topped up in the same step.
    """
    row = CoderStanding.objects.create(
        user=user, codebook_version=version, status=status, changed_by=by, note=note
    )
    if status in (CoderStanding.PAUSED, CoderStanding.REMOVED):
        cohorts = ClassificationCohort.objects.filter(
            codebook_version=version, round__isnull=False
        ).exclude(round__kind=Round.PRACTICE)
        ClassificationAssignment.objects.filter(
            cohort__in=cohorts, assigned_to=user, completed_at__isnull=True
        ).delete()
        for cohort in cohorts.filter(closed_at__isnull=True):
            assign_cohort(cohort)
    return row


def counted_decisions(version):
    """The decisions that count toward agreement and training under one
    codebook: not practice, not a check story, and not from a removed
    coder.

    One place, so no figure can be computed over answers another figure
    leaves out. Decisions from before versions existed carry none and
    are read as V1.
    """
    under = models.Q(codebook_version=version)
    if version.name == "V1":
        under |= models.Q(codebook_version__isnull=True)
    removed = [
        user_id
        for user_id, status in standings(version).items()
        if status == CoderStanding.REMOVED
    ]
    return (
        ClassificationDecision.objects.filter(under, is_check=False)
        .exclude(cohort__round__kind=Round.PRACTICE)
        .exclude(decided_by_id__in=removed)
    )


def _pairs(decisions):
    """Every unordered pair of decisions on the same article."""
    from itertools import combinations

    by_article = {}
    for d in decisions:
        by_article.setdefault(d.article_id, []).append(d)
    for made in by_article.values():
        yield from combinations(made, 2)


def as_answer(decision):
    """A decision as `cin_metrics` reads one."""
    return {
        "coder": decision.decided_by_id,
        "label": decision.primary_label,
        "secondary": decision.secondary_label,
    }


def answers_by_story(decisions):
    """story id -> its answers in the order they were given."""
    by_story = {}
    for decision in sorted(decisions, key=lambda d: (d.decided_at, d.pk)):
        by_story.setdefault(decision.article_id, []).append(as_answer(decision))
    return by_story


def v1_answers(story_ids=None):
    """The 2023 V1 coding as answers, for the same calculations.

    Groups A to D only, in that order. Group E was a third coder brought
    in on stories the first two disagreed about, so its pairs exist only
    where there was already a disagreement.
    """
    rows = V1Decision.objects.exclude(group="E").order_by("story_id", "group")
    if story_ids is not None:
        rows = rows.filter(story_id__in=list(story_ids))
    by_story = {}
    for row in rows:
        by_story.setdefault(row.story_id, []).append(
            {
                "coder": f"v1-{row.group}",
                "label": row.primary_label,
                "secondary": row.secondary_label,
            }
        )
    return by_story


def _agree(a, b):
    """Whether two coders matched, by the scoring rule: True, False, or
    None where the pair is not scored.

    Exact agreement on the primary label. Not set overlap: the primary
    is what the model is trained on, and a pair matching only on
    secondaries has not agreed about what the story is.

    A category against a rejection is a mismatch. Two rejections are not
    scored either way: a story neither coder would label is not a story
    to label. This used to count two matching rejections as agreement,
    which let a batch of unreadable stories raise the figure the
    definitions are judged by. See `cin_metrics`.
    """
    return cin_metrics.score(as_answer(a), as_answer(b))


def agreement_report(codebook_version=None):
    """Agreement overall, by dataset and by category, under ONE codebook.

    Answers given under different definitions are never pooled: a rate
    over V1 and V2 answers together is a rate under no codebook at all.
    Defaults to the current version. Practice rounds and removed
    coders are left out (`counted_decisions`).

    Percent agreement, and not kappa. Kappa needs a chance-agreement
    term over a fixed category distribution, and this queue oversamples
    low-incidence categories deliberately -- so the distribution is a
    sampling choice, not a property of the corpus, and a kappa computed
    against it would move when the sampling changed rather than when
    the coding did. The historical kappa (0.583) was computed once,
    over a fixed set, and is reported in the codebook annotations
    instead.
    """
    version = codebook_version or CodebookVersion.current()
    decisions = list(
        counted_decisions(version).select_related("cohort").order_by("article_id")
    )
    # Scored pairs only: two rejections are not a pair to score.
    pairs = [(a, b) for a, b in _pairs(decisions) if _agree(a, b) is not None]

    by_category, by_cohort = {}, {}
    agreed = 0
    for a, b in pairs:
        ok = _agree(a, b)
        agreed += ok
        # Attributed to both answers when they differ: a disagreement
        # between Civic information and Civic Life is a fact about both,
        # and charging it only to the first would understate whichever
        # category happens to sort earlier.
        for label in {a.primary_label, b.primary_label} - {""}:
            hit, total = by_category.get(label, (0, 0))
            by_category[label] = (hit + ok, total + 1)
        number = a.cohort.number
        hit, total = by_cohort.get(number, (0, 0))
        by_cohort[number] = (hit + ok, total + 1)

    def rate(hit, total):
        return round(100 * hit / total, 1) if total else None

    return {
        "version": version,
        "versions": list(CodebookVersion.objects.all()),
        "pairs": len(pairs),
        "articles_multiply_coded": len({a.article_id for a, _ in pairs}),
        "decisions": len(decisions),
        "overall": rate(agreed, len(pairs)),
        "categories": sorted(
            (
                {
                    "label": label,
                    "shown": version.shown(label),
                    "rate": rate(hit, total),
                    "pairs": total,
                }
                for label, (hit, total) in by_category.items()
            ),
            key=lambda row: row["rate"] if row["rate"] is not None else 101,
        ),
        "cohorts": sorted(
            (
                {"number": number, "rate": rate(hit, total), "pairs": total}
                for number, (hit, total) in by_cohort.items()
            ),
            key=lambda row: -row["number"],
        ),
    }


def coder_report():
    """Who can classify, what each is granted, how much each has done, and
    the state of every cohort.

    Also the pool to choose from. "Users with the classifier role" cannot
    mean "holds an unscoped classifier grant", because an unscoped grant
    is an application-wide one -- see `grant_cohort`. A classifier IS
    somebody granted a cohort, so the pool to offer is the console's
    users, and granting a cohort is what makes one.
    """
    from django.contrib.auth import get_user_model

    from accounts.models import DATADESK, Grant, Invitation

    counts, outstanding = {}, {}
    for user_id in ClassificationDecision.objects.values_list(
        "decided_by_id", flat=True
    ):
        counts[user_id] = counts.get(user_id, 0) + 1
    for user_id in ClassificationAssignment.objects.filter(
        completed_at__isnull=True
    ).values_list("assigned_to_id", flat=True):
        outstanding[user_id] = outstanding.get(user_id, 0) + 1

    # Every cohort each classifier holds, so one row per person rather
    # than one per grant: a coder on three cohorts read as three coders.
    held = {}
    for grant in (
        Grant.objects.filter(app=DATADESK, role=CLASSIFIER)
        .select_related("user")
        .order_by("scope")
    ):
        held.setdefault(grant.user, []).append(grant.scope)

    coders = sorted(
        (
            {
                "user": user,
                "cohorts": scopes,
                "done": counts.get(user.id, 0),
                "outstanding": outstanding.get(user.id, 0),
            }
            for user, scopes in held.items()
        ),
        key=lambda row: (-row["done"], str(row["user"])),
    )

    cohorts = []
    for cohort in ClassificationCohort.objects.order_by("-number"):
        drawn = ClassificationSample.objects.filter(cohort=cohort).count()
        assignments = ClassificationAssignment.objects.filter(cohort=cohort)
        made = assignments.count()
        done = assignments.filter(completed_at__isnull=False).count()
        granted = len(coders_for(cohort))
        # What the cohort still needs to be usable for training: every
        # record disposed of by `target_coders` people.
        needed = drawn * min(cohort.target_coders, granted or cohort.target_coders)
        cohorts.append(
            {
                "cohort": cohort,
                "drawn": drawn,
                "granted": granted,
                "assignments": made,
                "done": done,
                "needed": needed,
                "short": max(needed - made, 0),
                "percent": round(100 * done / made, 1) if made else None,
            }
        )

    return {
        "coders": coders,
        "total": sum(row["done"] for row in coders),
        "cohorts": cohorts,
        "open_cohorts": [row for row in cohorts if row["cohort"].closed_at is None],
        # Offered in the picker. Superusers included: somebody has to be
        # able to work a cohort on a fresh install.
        "candidates": get_user_model().objects.order_by("username"),
        # Invited and not yet signed in. Without this they are invisible:
        # no user row exists, so they appear in no list, and an admin
        # who invited somebody last week has no way to see that they
        # have not turned up.
        "pending": Invitation.objects.filter(
            app=DATADESK, role=CLASSIFIER, accepted_at__isnull=True
        ).order_by("email"),
    }


# ------------------------------------------------- drawing and assigning
#
# The models existed and nothing filled them. A cohort had to be made in
# a shell, its articles inserted by hand, and its coders granted one SQL
# statement at a time -- so the queue could be worked but never started.


#: How a cohort is divided between the four strata. Equal quarters, and
#: confident/uncertain equal to each other by construction: the whole
#: point of those two is a direct comparison, which an unequal draw
#: blurs. `docs/CLASSIFICATION_REVIEW_QUEUE.md` sets out why.
STRATUM_SHARE = {
    ClassificationSample.CONFIDENT: 0.25,
    ClassificationSample.UNCERTAIN: 0.25,
    ClassificationSample.RANDOM: 0.25,
    ClassificationSample.UNLABELLED: 0.25,
}

#: The confidence bands. The middle (0.5-0.7) is deliberately in neither:
#: it is neither claim, and including it blurs the one thing these two
#: strata exist to separate. Those rows stay reachable at random.
CONFIDENT_AT_OR_ABOVE = 0.7
UNCERTAIN_BELOW = 0.5

#: What the crawler considers classifiable -- the same statuses
#: `analysis.py` feeds the model, so the queue reviews the population the
#: model actually sees rather than a wider one.
#:
#: `labeled` and `enriched` are where a story goes once the model has
#: labelled it: about 100,500 stories, against 321 still in `cleaned`.
#: A draw limited to the first two statuses saw almost none of the
#: corpus and none of what the model had already answered.
CLASSIFIABLE_STATUSES = ("cleaned", "local", "labeled", "enriched")

#: The datasets stories are drawn from. Vermont is left out: student
#: newspapers, not fully collected or labelled.
DRAW_DATASETS = (
    "Mizzou-Missouri-State",
    "WSU-Washington-State",
    "Penn-State-Lehigh",
)
#: Missouri is 96% of labelled stories. Drawn without regard to state, a
#: training set is Missouri alone and the model is then used on all
#: three. So each category takes up to this many from each of the two
#: smaller datasets, and the rest from Missouri.
SMALL_DATASETS = ("WSU-Washington-State", "Penn-State-Lehigh")
PER_SMALL_DATASET = 60


def draw_dataset_ids():
    """slug -> id for the datasets a draw may use, where they exist."""
    from explorer.models import Dataset

    return dict(
        Dataset.objects.using("crawler")
        .filter(slug__in=DRAW_DATASETS)
        .values_list("slug", "id")
    )


def _pool():
    """Every crawler story a draw may take."""
    from explorer.models import Article

    rows = Article.objects.using("crawler").filter(status__in=CLASSIFIABLE_STATUSES)
    # Where none of the named datasets exists -- another installation, a
    # test corpus -- there is nothing to restrict to.
    ids = list(draw_dataset_ids().values())
    return rows.filter(dataset_id__in=ids) if ids else rows


def _stratum_pool(stratum):
    """The articles a stratum may draw from, as an unordered queryset."""
    rows = _pool()
    if stratum == ClassificationSample.CONFIDENT:
        return rows.filter(primary_label_confidence__gte=CONFIDENT_AT_OR_ABOVE)
    if stratum == ClassificationSample.UNCERTAIN:
        return rows.filter(
            primary_label_confidence__lt=UNCERTAIN_BELOW,
            primary_label_confidence__isnull=False,
        )
    if stratum == ClassificationSample.UNLABELLED:
        return rows.filter(primary_label__isnull=True)
    # Random draws from everything classifiable, including the middle
    # band and the rows the other strata already cover. That overlap is
    # the point: it is the only stratum whose rate is an unbiased
    # estimate of anything.
    return rows


def draw_cohort(
    size,
    *,
    target_coders=3,
    note="",
    stale_after_days=14,
    codebook_version=None,
    for_round=None,
):
    """Open a cohort and draw `size` articles into it.

    The cohort is coded under `codebook_version`, or the current one.
    Opened for a round (`for_round`, not `round`: the draw needs the
    builtin) it takes the round's codebook and its coders
    per story, and the coders already on the round's other batches are
    granted this one, so a batch is not a fresh round of invitations.

    Returns (cohort, drawn) where `drawn` maps each stratum to how many it
    actually contributed. A short stratum is reported rather than
    silently topped up from elsewhere: a cohort whose unlabelled quarter
    came out of the random pool is not the cohort the design describes,
    and a caller that cannot see the difference cannot correct for it.

    Articles already drawn into any earlier cohort are excluded. Cohort
    membership is permanent, and one article in two cohorts would make
    each of their rates depend on the other.
    """
    from django.db import transaction

    if for_round is not None:
        codebook_version = for_round.codebook_version
        target_coders = for_round.base_coders
    taken = set(ClassificationSample.objects.values_list("article_id", flat=True))
    with transaction.atomic():
        number = (
            ClassificationCohort.objects.aggregate(models.Max("number"))["number__max"]
            or 0
        ) + 1
        cohort = ClassificationCohort.objects.create(
            number=number,
            slug=f"cohort-{number}",
            target_coders=target_coders,
            stale_after_days=stale_after_days,
            note=note,
            codebook_version=codebook_version or CodebookVersion.current(),
            round=for_round,
        )

        drawn, samples = {}, []
        for stratum, share in STRATUM_SHARE.items():
            want = int(round(size * share))
            available = _stratum_pool(stratum).exclude(id__in=taken)
            # Counted before the slice: the chance a row in this stratum
            # had of being drawn cannot be recovered later, and without it
            # an oversampled draw cannot be weighted back to corpus rates.
            total = available.count()
            rows = list(
                available.order_by("?").values_list(
                    "id", "dataset_id", "primary_label", "primary_label_confidence"
                )[:want]
            )
            probability = (len(rows) / total) if total else 1.0
            for article_id, dataset_id, label, confidence in rows:
                taken.add(article_id)
                samples.append(
                    ClassificationSample(
                        cohort=cohort,
                        article_id=article_id,
                        stratum=stratum,
                        inclusion_probability=probability,
                        dataset_id=dataset_id or "",
                        model_label=label or "",
                        model_confidence=confidence,
                    )
                )
            drawn[stratum] = len(rows)

        ClassificationSample.objects.bulk_create(samples, batch_size=500)
    if for_round is not None:
        for user in round_coders(for_round):
            grant_cohort(user, cohort)
    return cohort, drawn


def coders_for(cohort):
    """The users granted this cohort, in a stable order."""
    from accounts.models import DATADESK, Grant

    return [
        grant.user
        for grant in Grant.objects.filter(
            app=DATADESK, role=CLASSIFIER, scope=cohort.slug
        )
        .select_related("user")
        .order_by("user__username")
    ]


def grant_cohort(user, cohort):
    """Give one classifier one cohort. Idempotent.

    The scope is always a cohort slug and never blank. `WHOLE_APPLICATION`
    is the empty string and `permitted_scopes` returns ALL_SCOPES for it,
    so a classifier granted with no scope could work every cohort there
    is -- the opposite of the point. Controlling which records a coder is
    given is the reason this role is scoped at all.
    """
    from accounts.models import DATADESK, WHOLE_APPLICATION, Grant

    if not cohort.slug or cohort.slug == WHOLE_APPLICATION:
        raise ValueError("a classifier grant must name a cohort")
    grant, _ = Grant.objects.get_or_create(
        user=user, app=DATADESK, role=CLASSIFIER, scope=cohort.slug
    )
    return grant


def revoke_cohort(user, cohort):
    """Take it back, and withdraw anything outstanding.

    Completed decisions stay: they are data. Outstanding assignments do
    not, or the cohort waits forever on somebody who can no longer open
    it -- the quiet way this design fails.
    """
    from accounts.models import DATADESK, Grant

    Grant.objects.filter(
        user=user, app=DATADESK, role=CLASSIFIER, scope=cohort.slug
    ).delete()
    return ClassificationAssignment.objects.filter(
        cohort=cohort, assigned_to=user, completed_at__isnull=True
    ).delete()[0]


def assign_cohort(cohort):
    """Hand every record in the cohort to `target_coders` coders.

    Round-robin over the granted coders, so each carries within one record
    of the same load -- "assign coders to assure each gets the same
    number" is the requirement, and an uneven split makes a cohort finish
    at the pace of its slowest member.

    Nobody is given the same record twice: a `target_coders` above the
    number granted assigns as many as exist rather than duplicating,
    because two decisions from one person on one article is not the
    overlap agreement is measured over.

    Idempotent. Re-running after granting a fourth coder tops the cohort
    up to target rather than starting again.

    Inside a round, who is assigned depends on the round's kind and each
    coder's standing; see `CoderStanding`.
    """
    from django.db import transaction

    coders = coders_for(cohort)
    standing = standings(cohort.codebook)
    practice = cohort.round is not None and cohort.round.is_practice
    # A paused or removed coder takes no new work anywhere. In a counted
    # round -- a pilot or training -- a coder also has to have been
    # admitted, which means the practice and its review are behind them.
    coders = [
        user
        for user in coders
        if standing.get(user.id) not in (CoderStanding.PAUSED, CoderStanding.REMOVED)
        and (
            cohort.round is None
            or practice
            or standing.get(user.id) == CoderStanding.ADMITTED
        )
    ]
    if not coders:
        return 0

    # A removed coder's answers are withdrawn, so their assignments do
    # not count toward a story's coders and the story is topped up.
    removed = {
        user_id
        for user_id, status in standing.items()
        if status == CoderStanding.REMOVED
    }
    existing = {}
    for user_id, article_id in ClassificationAssignment.objects.filter(
        cohort=cohort, is_check=False
    ).values_list("assigned_to_id", "article_id"):
        if user_id not in removed:
            existing.setdefault(article_id, set()).add(user_id)

    # Practice is every coder on every story: the review goes through
    # each disagreement with the people who made it.
    wanted = len(coders) if practice else min(cohort.target_coders, len(coders))
    made, offset = [], 0
    article_ids = list(
        ClassificationSample.objects.filter(cohort=cohort)
        .order_by("id")
        .values_list("article_id", flat=True)
    )
    # Shuffled, the same way every time for one cohort. Samples are
    # stored group by group -- a pilot's civic stories, then the others;
    # a training batch category by category -- and a coder who meets
    # forty Education stories in a row has been told what the next one
    # is. A queue is worked in the order assignments were made.
    import random

    random.Random(cohort.pk).shuffle(article_ids)
    for article_id in article_ids:
        already = existing.get(article_id, set())
        # Rotate the starting point per article so the same pair does not
        # share every record: overlap spread across the group is what lets
        # agreement be read per coder rather than for one pairing.
        start = offset % len(coders)
        order = coders[start:] + coders[:start]
        offset += 1
        for user in order:
            if len(already) >= wanted:
                break
            if user.id in already:
                continue
            already.add(user.id)
            made.append(
                ClassificationAssignment(
                    cohort=cohort, article_id=article_id, assigned_to=user
                )
            )

    with transaction.atomic():
        ClassificationAssignment.objects.bulk_create(made, batch_size=500)
    return len(made)


def invite_coder(email, cohort, *, invited_by=None):
    """Admit somebody to one cohort as a classifier, before they exist.

    The flow without this was backwards: a coder had to be invited to a
    dataset as a viewer, sign in so a user row existed, and only then be
    given a cohort -- three steps to grant one role, two of which handed
    them access nobody wanted them to have.

    `Invitation` already carries a role and a scope, and the adapter makes
    exactly that grant on first sign-in. Nothing exposed it for cohorts,
    and `accounts.views.invite` refuses an address inside an allowed
    domain outright -- correctly, for admission, since the domain already
    admits them, but that also blocks pre-assigning a role to somebody who
    has never signed in. Admission and role are different questions.

    Returns (kind, obj) where kind is "granted" when the user already
    exists and the grant was made now, or "invited" when the row waits for
    a first sign-in.
    """
    from django.contrib.auth import get_user_model

    from accounts.models import DATADESK, Invitation

    email = (email or "").strip().lower()
    if "@" not in email:
        raise ValueError("that is not an address")

    user = get_user_model().objects.filter(email__iexact=email).first()
    if user is not None:
        return "granted", grant_cohort(user, cohort)

    # One invitation per address, so a second cohort for the same person
    # updates the row rather than failing on the unique constraint. The
    # grant it makes is one cohort; the rest are granted after they sign
    # in, which is when a user exists to grant them to.
    invitation, created = Invitation.objects.update_or_create(
        email=email,
        defaults={
            "app": DATADESK,
            "role": CLASSIFIER,
            "scope": cohort.slug,
            "invited_by": invited_by,
        },
    )
    return "invited", invitation


# ------------------------------------------------------------------ rounds


def round_coders(round):
    """Everybody granted any batch of the round, in a stable order."""
    seen, out = set(), []
    for cohort in round.cohorts.order_by("number"):
        for user in coders_for(cohort):
            if user.id not in seen:
                seen.add(user.id)
                out.append(user)
    return sorted(out, key=lambda user: user.username)


def open_round(
    name, kind, *, codebook_version=None, question="", base_coders=3, **rules
):
    return Round.objects.create(
        name=name,
        kind=kind,
        codebook_version=codebook_version or CodebookVersion.current(),
        question=question,
        base_coders=base_coders,
        third_on_split=bool(rules.get("third_on_split")),
        draw_rule=rules.get("draw_rule") or {},
        finish_rule=rules.get("finish_rule") or {},
    )


def _agreed_label(decisions):
    """The label a story's answers settle on, or None: two coders with
    the same category, or one coder's category as another's secondary
    (`cin_metrics.settled_label`)."""
    return cin_metrics.settled_label([as_answer(d) for d in decisions])[1]


def round_report(round):
    """How far a round has got, measured against how it finishes.

    Never a bare total: a practice round is finished when every coder
    has coded every story and the review is held; a pilot when every
    story has its coders; a training round when every category has
    reached its floor, which a total reaches long before the rare
    categories do.
    """
    cohorts = list(round.cohorts.order_by("number"))
    version = round.codebook_version
    standing = standings(version)
    removed = {u for u, st in standing.items() if st == CoderStanding.REMOVED}
    stories = ClassificationSample.objects.filter(cohort__in=cohorts).count()
    assignments = list(
        ClassificationAssignment.objects.filter(cohort__in=cohorts).values_list(
            "assigned_to_id", "article_id", "completed_at"
        )
    )
    per_coder = {}
    for user_id, _, completed in assignments:
        given, done = per_coder.get(user_id, (0, 0))
        per_coder[user_id] = (given + 1, done + (completed is not None))
    coders = [
        {
            "user": user,
            "assigned": per_coder.get(user.id, (0, 0))[0],
            "done": per_coder.get(user.id, (0, 0))[1],
            "standing": standing.get(user.id, ""),
            "finished": bool(per_coder.get(user.id, (0, 0))[0])
            and per_coder.get(user.id, (0, 0))[0] == per_coder.get(user.id, (0, 0))[1],
        }
        for user in round_coders(round)
    ]

    decisions = [
        d
        for d in ClassificationDecision.objects.filter(
            cohort__in=cohorts, is_check=False
        )
        if d.decided_by_id not in removed
    ]
    by_story = {}
    for decision in decisions:
        by_story.setdefault(decision.article_id, []).append(decision)

    report = {
        "round": round,
        "cohorts": cohorts,
        "stories": stories,
        "coders": coders,
        "decisions": len(decisions),
    }
    if round.is_practice:
        everyone = bool(coders) and all(row["finished"] for row in coders)
        report.update(
            coded=everyone,
            reviewed=round.reviewed_at is not None,
            finished=everyone and round.reviewed_at is not None,
            waiting_on=[row["user"] for row in coders if not row["finished"]],
        )
    elif round.kind == Round.COMPARISON:
        full = sum(1 for made in by_story.values() if len(made) >= round.base_coders)
        report.update(
            complete_stories=full,
            percent=round_percent(full, stories),
            finished=bool(stories) and full == stories,
        )
    else:
        floor = int(round.finish_rule.get("per_category") or 0)
        counts = training_counts(round)
        hits = hit_rates(round)
        rows = [
            {
                "label": label,
                "shown": version.shown(label),
                "count": counts[label],
                "floor": floor,
                "percent": round_percent(min(counts[label], floor), floor),
                "hit_rate": hits.get(label),
            }
            for label in CIN_LABELS
        ]
        binding = min(rows, key=lambda row: row["count"]) if rows else None
        report.update(
            categories=rows,
            floor=floor,
            binding=binding,
            finished=bool(floor) and all(row["count"] >= floor for row in rows),
        )
    return report


def round_percent(part, whole):
    return round_half(100 * part / whole) if whole else None


def round_half(value):
    return int(value + 0.5)


def practice_review(round):
    """Every practice story with each coder's answer side by side.

    For the meeting that ends a practice round: the lead and the coders
    go through each story they answered differently, against the
    definitions. Empty until every coder has finished -- a coder who
    sees the others' answers first is no longer practising.
    """
    report = round_report(round)
    if not report.get("coded"):
        return {"ready": False, "rows": [], "report": report}
    version = round.codebook_version
    rejections = dict(ClassificationDecision.REJECTIONS)
    decisions = (
        ClassificationDecision.objects.filter(cohort__in=report["cohorts"])
        .select_related("decided_by")
        .order_by("article_id", "decided_by__username")
    )
    by_story = {}
    for decision in decisions:
        by_story.setdefault(decision.article_id, []).append(decision)
    titles = {key: story.title for key, story in stories_for(by_story).items()}
    guide = {row["label"]: row for row in version.guidance_rows()}
    rows = []
    for article_id, made in by_story.items():
        answers = [
            {
                "coder": d.decided_by,
                "answer": (
                    version.shown(d.primary_label)
                    if d.primary_label
                    else rejections.get(d.reject_reason, d.reject_reason)
                ),
                "secondary": (
                    version.shown(d.secondary_label) if d.secondary_label else ""
                ),
                "rejected": not d.primary_label,
            }
            for d in made
        ]
        distinct = sorted({a["answer"] for a in answers})
        rows.append(
            {
                "article_id": article_id,
                "title": titles.get(article_id) or "(no headline)",
                "answers": answers,
                "differ": len(distinct) > 1,
                "definitions": [guide[name] for name in distinct if name in guide],
            }
        )
    rows.sort(key=lambda row: (not row["differ"], row["title"]))
    return {
        "ready": True,
        "rows": rows,
        "differing": sum(row["differ"] for row in rows),
        "report": report,
    }


def hold_review(round, *, by, notes=""):
    """Record that the practice review was held, and admit the coders
    who had finished the practice when it was."""
    from django.utils import timezone

    report = round_report(round)
    round.reviewed_at = timezone.now()
    round.reviewed_by = by
    round.review_notes = notes
    round.save(update_fields=["reviewed_at", "reviewed_by", "review_notes"])
    admitted = []
    for row in report["coders"]:
        if row["finished"] and row["standing"] != CoderStanding.ADMITTED:
            set_standing(
                row["user"],
                round.codebook_version,
                CoderStanding.ADMITTED,
                by=by,
                note=f"practice reviewed: {round.name}",
            )
            admitted.append(row["user"])
    return admitted


# ------------------------------------------------------------ round draws


def _open_batch(for_round, samples, note):
    """A cohort in a round holding exactly these samples."""
    from django.db import transaction

    with transaction.atomic():
        number = (
            ClassificationCohort.objects.aggregate(models.Max("number"))["number__max"]
            or 0
        ) + 1
        cohort = ClassificationCohort.objects.create(
            number=number,
            slug=ClassificationCohort.slug_for(number),
            target_coders=for_round.base_coders,
            note=note,
            codebook_version=for_round.codebook_version,
            round=for_round,
        )
        for sample in samples:
            sample.cohort = cohort
        ClassificationSample.objects.bulk_create(samples, batch_size=500)
    for user in round_coders(for_round):
        grant_cohort(user, cohort)
    return cohort


def draw_pilot(for_round, *, others=400, seed=0):
    """The pilot's stories: every civic V1 story, and `others` of the rest.

    All from the V1 coding, so a V2 answer is compared with the V1
    answers on the same story. The stories two V1 coders rejected are
    left out -- they are not stories to label -- and the ones V1 coders
    never agreed on stay in: they are what the old definitions failed
    on, and leaving them out would flatter V1.

    The civic stories are taken whole. The others are a random draw,
    there so the round reads as ordinary local news and no coder has a
    reason to expect one category; the chance each had of being drawn is
    recorded with it.
    """
    import random

    taken = set(ClassificationSample.objects.values_list("article_id", flat=True))
    usable = ClassificationStory.objects.filter(origin=ClassificationStory.V1).exclude(
        v1_outcome=ClassificationStory.REJECTED
    )
    civic = [s.id for s in usable.filter(civic=True) if s.id not in taken]
    rest = sorted(s.id for s in usable.filter(civic=False) if s.id not in taken)
    chosen = random.Random(seed).sample(rest, min(others, len(rest)))
    probability = (len(chosen) / len(rest)) if rest else 1.0
    samples = [
        ClassificationSample(
            article_id=story_id,
            stratum=ClassificationSample.CIVIC,
            inclusion_probability=1.0,
            dataset_id=ClassificationStory.V1,
        )
        for story_id in civic
    ] + [
        ClassificationSample(
            article_id=story_id,
            stratum=ClassificationSample.OTHER,
            inclusion_probability=probability,
            dataset_id=ClassificationStory.V1,
        )
        for story_id in chosen
    ]
    if not samples:
        return None, {"civic": 0, "other": 0}
    cohort = _open_batch(for_round, samples, "pilot: V1 stories")
    return cohort, {"civic": len(civic), "other": len(chosen)}


def training_counts(for_round):
    """label -> stories that count toward a training round's floor.

    V1 records count first, and only those with a label: the label V2
    coders agreed on where the story was in a pilot under this codebook,
    else the label V1 coders agreed on. A V1 story that was rejected, or
    has no agreed label, is not training data and is not counted. Then
    the round's own stories two coders agreed on.
    """
    version = for_round.codebook_version
    counts = dict.fromkeys(CIN_LABELS, 0)
    by_story = {}
    for decision in counted_decisions(version):
        by_story.setdefault(decision.article_id, []).append(decision)

    piloted = set(
        ClassificationSample.objects.filter(
            cohort__round__kind=Round.COMPARISON, cohort__codebook_version=version
        ).values_list("article_id", flat=True)
    )
    for story in ClassificationStory.objects.filter(origin=ClassificationStory.V1):
        if story.id in piloted:
            label = _agreed_label(by_story.get(story.id, []))
        elif story.v1_outcome in (
            ClassificationStory.LABEL_09,
            ClassificationStory.LABEL_07,
        ):
            label = story.v1_label
        else:
            label = None
        if label in counts:
            counts[label] += 1

    ours = set(
        ClassificationSample.objects.filter(cohort__round=for_round).values_list(
            "article_id", flat=True
        )
    )
    for story_id in ours:
        label = _agreed_label(by_story.get(story_id, []))
        if label in counts:
            counts[label] += 1
    return counts


def hit_rates(for_round):
    """label -> the share of stories drawn for a category that coders
    placed in it, or None where nothing has been settled yet.

    Stories are drawn by the model's label and the model is not always
    right, so reaching a floor takes more draws than the floor. How many
    more is measured here, from stories that have two answers.
    """
    version = for_round.codebook_version
    by_story = {}
    for decision in counted_decisions(version).filter(cohort__round=for_round):
        by_story.setdefault(decision.article_id, []).append(decision)
    drawn, hit = {}, {}
    for story_id, label in ClassificationSample.objects.filter(
        cohort__round=for_round, stratum=ClassificationSample.CATEGORY
    ).values_list("article_id", "model_label"):
        made = by_story.get(story_id, [])
        if len(made) < 2:
            continue
        drawn[label] = drawn.get(label, 0) + 1
        hit[label] = hit.get(label, 0) + (_agreed_label(made) == label)
    return {
        label: (hit.get(label, 0) / drawn[label] if drawn.get(label) else None)
        for label in CIN_LABELS
    }


#: A category whose measured hit rate is zero would ask for unbounded
#: draws. Below this it is treated as this.
LOWEST_HIT_RATE = 0.05


def training_plan(for_round, size):
    """How a batch of `size` is divided between the categories.

    Each category needs (floor - what it has) more labels, and gets them
    at its hit rate, so it needs that many divided by the rate in draws.
    The batch is shared in proportion to those. Before anything has been
    measured every rate is taken as 1, which makes the first batch a
    plain division by need -- and that batch is what measures the rates.
    """
    import math

    floor = int(for_round.finish_rule.get("per_category") or 0)
    have = training_counts(for_round)
    rates = hit_rates(for_round)
    rows = []
    for label in CIN_LABELS:
        need = max(0, floor - have[label])
        rate = rates[label]
        wanted = need / max(rate if rate is not None else 1.0, LOWEST_HIT_RATE)
        rows.append(
            {
                "label": label,
                "have": have[label],
                "need": need,
                "rate": rate,
                "wanted": wanted,
            }
        )
    total = sum(row["wanted"] for row in rows)
    for row in rows:
        share = size * row["wanted"] / total if total else 0
        row["draw"] = min(math.ceil(row["wanted"]), int(share + 0.5))
    return rows


def draw_training_batch(for_round, size=300):
    """A batch of new stories, by category and by state.

    Each category's share (`training_plan`) is taken from the model's
    label, and the round's held-back share of them (`holdout` over
    `per_category` in its finish rule) is marked as it is drawn. Within
    it, up to `PER_SMALL_DATASET` stories over the whole
    round come from each of the smaller datasets, a third of the share
    at a time, and the rest from Missouri; where a dataset has fewer
    than that in a category, all of them.
    """
    import math

    plan = training_plan(for_round, size)
    floor = int(for_round.finish_rule.get("per_category") or 0)
    held_back = int(for_round.finish_rule.get("holdout") or 0) / floor if floor else 0.0
    holdout = random.Random(for_round.pk * 1000 + for_round.cohorts.count())
    datasets = draw_dataset_ids()
    small = [datasets[slug] for slug in SMALL_DATASETS if slug in datasets]
    taken = set(ClassificationSample.objects.values_list("article_id", flat=True))
    already = {}
    for label, dataset_id in ClassificationSample.objects.filter(
        cohort__round=for_round, stratum=ClassificationSample.CATEGORY
    ).values_list("model_label", "dataset_id"):
        already[(label, dataset_id)] = already.get((label, dataset_id), 0) + 1

    samples = []
    for row in plan:
        label, want = row["label"], row["draw"]
        row["drawn"] = 0
        if not want:
            continue
        pool = _pool().filter(primary_label=label).exclude(id__in=taken)
        parts = []
        for dataset_id in small:
            quota = PER_SMALL_DATASET - already.get((label, dataset_id), 0)
            parts.append(
                (pool.filter(dataset_id=dataset_id), min(math.ceil(want / 3), quota))
            )
        for rows, limit in parts + [(pool.exclude(dataset_id__in=small), None)]:
            limit = want - row["drawn"] if limit is None else limit
            limit = max(0, min(limit, want - row["drawn"]))
            if not limit:
                continue
            total = rows.count()
            picked = list(
                rows.order_by("?").values_list(
                    "id", "dataset_id", "primary_label_confidence"
                )[:limit]
            )
            probability = (len(picked) / total) if total else 1.0
            for article_id, dataset_id, confidence in picked:
                taken.add(article_id)
                samples.append(
                    ClassificationSample(
                        article_id=article_id,
                        stratum=ClassificationSample.CATEGORY,
                        inclusion_probability=probability,
                        dataset_id=dataset_id or "",
                        model_label=label,
                        model_confidence=confidence,
                        # Already in random order, so the share held
                        # back is a random share.
                        holdout=holdout.random() < held_back,
                    )
                )
            row["drawn"] += len(picked)
    if not samples:
        return None, plan
    return _open_batch(for_round, samples, "training batch"), plan


# ------------------------------------------------------------------- gates

#: The reliability the pilot has to show, and what that means in the
#: terms coders are scored in. Chance agreement across the ten categories
#: is about 0.15, from the V1 coding (64.5% raw agreement at kappa 0.583),
#: so an alpha of 0.80 needs the first two coders to match on 83% of
#: stories; and at 83% overall the average category sits at 71%.
RELIABILITY_TARGET = 0.80
CHANCE_AGREEMENT = 0.15
GATE_FIRST_PAIR = round(
    cin_metrics.raw_agreement_for_alpha(RELIABILITY_TARGET, CHANCE_AGREEMENT), 2
)
GATE_PER_CATEGORY = round(cin_metrics.per_category_floor(GATE_FIRST_PAIR), 2)
#: The two categories the V2 definitions were written to separate.
GATE_CATEGORIES = ("Civic information", "Civic Life")


def _condition(name, population, measured, threshold, before=None):
    rate = measured.get("rate")
    return {
        "name": name,
        "population": population,
        "value": rate,
        "low": measured.get("low"),
        "high": measured.get("high"),
        "n": measured.get("n", 0),
        "threshold": threshold,
        "v1": before,
        "outcome": (
            GateResult.NOT_ENOUGH
            if rate is None
            else (GateResult.PASS if rate >= threshold else GateResult.FAIL)
        ),
    }


def round_answers(for_round):
    """story id -> counted answers in a round, in the order given."""
    return answers_by_story(
        counted_decisions(for_round.codebook_version).filter(cohort__round=for_round)
    )


def _pilot_conditions(for_round, stories):
    """The four numbers a pilot has to reach.

    Each is first-pair agreement: the share of stories on which the
    first two coders to read it chose the same category. Each is a fixed
    threshold on the observed rate, with its interval reported beside
    it. A pilot drawn from the V1 stories is scored in its two groups,
    and each is shown against what V1 coders did on the same stories.
    """
    version = for_round.codebook_version
    strata = dict(
        ClassificationSample.objects.filter(cohort__round=for_round).values_list(
            "article_id", "stratum"
        )
    )
    before = v1_answers(strata)
    groups = {}
    for name in (ClassificationSample.CIVIC, ClassificationSample.OTHER):
        ids = {story for story, stratum in strata.items() if stratum == name}
        if ids:
            groups[name] = (
                {k: v for k, v in stories.items() if k in ids},
                {k: v for k, v in before.items() if k in ids},
                len(ids),
            )

    def v1_rate(subset, label=None):
        rate = cin_metrics.first_pair_agreement(subset, label)["rate"]
        return rate if subset else None

    conditions = []
    if ClassificationSample.CIVIC in groups:
        now, then, size = groups[ClassificationSample.CIVIC]
        conditions.append(
            _condition(
                "The civic stories",
                f"{size} V1 stories a V1 coder put in either civic category",
                cin_metrics.first_pair_agreement(now),
                GATE_FIRST_PAIR,
                v1_rate(then),
            )
        )
    for label in GATE_CATEGORIES:
        conditions.append(
            _condition(
                version.shown(label),
                "stories either of the first two coders put in it",
                cin_metrics.first_pair_agreement(stories, label),
                GATE_PER_CATEGORY,
                v1_rate(before, label),
            )
        )
    if ClassificationSample.OTHER in groups:
        now, then, size = groups[ClassificationSample.OTHER]
        conditions.append(
            _condition(
                "The other stories",
                f"{size} other V1 stories",
                cin_metrics.first_pair_agreement(now),
                GATE_FIRST_PAIR,
                v1_rate(then),
            )
        )

    if len(groups) == 2:
        # Counted at the civic stories' share of the V1 stories kept, not
        # their larger share of the pilot, which holds all of them.
        kept = ClassificationStory.objects.filter(
            origin=ClassificationStory.V1
        ).exclude(v1_outcome=ClassificationStory.REJECTED)
        share = kept.filter(civic=True).count() / (kept.count() or 1)
        civic, other = groups[ClassificationSample.CIVIC], groups["other"]
        overall = cin_metrics.weighted_agreement(
            [(civic[0], share), (other[0], 1 - share)]
        )
        then = cin_metrics.weighted_agreement(
            [(civic[1], share), (other[1], 1 - share)]
        )["rate"]
        population = f"civic stories counted at {share:.0%} of the whole"
    else:
        overall = cin_metrics.first_pair_agreement(stories)
        then = v1_rate(before)
        population = "every story in the round"
    conditions.append(
        _condition("All stories", population, overall, GATE_FIRST_PAIR, then)
    )
    return conditions, groups


def gate(for_round, *, by=None, record=False):
    """Measure a round against its finish line. With `record`, write it
    down as a `GateResult`.

    The outcome is NOT_ENOUGH while the round is unfinished -- stories
    still short of their coders, a review not yet held -- however the
    figures look so far: a rate over the stories coded first is a rate
    over the easy ones.
    """
    report = round_report(for_round)
    stories = round_answers(for_round)
    measures = {}

    if for_round.is_practice:
        conditions = [
            {
                "name": "Every coder has coded every story",
                "outcome": (
                    GateResult.PASS if report["coded"] else GateResult.NOT_ENOUGH
                ),
            },
            {
                "name": "The review has been held",
                "outcome": (
                    GateResult.PASS if report["reviewed"] else GateResult.NOT_ENOUGH
                ),
            },
        ]
    elif for_round.kind == Round.COMPARISON:
        conditions, groups = _pilot_conditions(for_round, stories)
        labelled = sum(
            1 for answers in stories.values() if cin_metrics.settled_label(answers)[1]
        )
        coded = sum(1 for answers in stories.values() if len(answers) >= 2)
        measures = {
            "alpha": cin_metrics.krippendorff_alpha(stories),
            "alpha_target": RELIABILITY_TARGET,
            "labelled_share": labelled / coded if coded else None,
            "left_out": cin_metrics.left_out(stories),
        }
        if record and ClassificationSample.CIVIC in groups:
            now, then, _ = groups[ClassificationSample.CIVIC]
            measures["civic_change"] = cin_metrics.paired_change(now, then)
    else:
        conditions = [
            {
                "name": row["shown"],
                "population": "labelled stories, V1 records counted first",
                "value": row["count"],
                "threshold": row["floor"],
                "outcome": (
                    GateResult.PASS
                    if row["floor"] and row["count"] >= row["floor"]
                    else GateResult.NOT_ENOUGH
                ),
            }
            for row in report["categories"]
        ]
        measures = {
            "first_pair": cin_metrics.first_pair_agreement(stories),
            "first_pair_floor": GATE_FIRST_PAIR,
            "left_out": cin_metrics.left_out(stories),
        }

    outcomes = {c["outcome"] for c in conditions}
    unfinished = for_round.kind == Round.COMPARISON and not report.get("finished")
    if unfinished or GateResult.NOT_ENOUGH in outcomes:
        outcome = GateResult.NOT_ENOUGH
    elif GateResult.FAIL in outcomes:
        outcome = GateResult.FAIL
    else:
        outcome = GateResult.PASS

    result = {"outcome": outcome, "conditions": conditions, "measures": measures}
    if record:
        result["record"] = GateResult.objects.create(
            round=for_round,
            evaluated_by=by,
            outcome=outcome,
            conditions=conditions,
            measures=measures,
        )
    return result


def coder_figures(for_round):
    """Per coder in a round: agreement with the others on first pairs,
    and pace. For admins. A coder is never shown theirs or anyone's: a
    coder who can watch their agreement codes toward the others."""
    decisions = list(
        counted_decisions(for_round.codebook_version).filter(cohort__round=for_round)
    )
    agreement = cin_metrics.coder_agreement(answers_by_story(decisions))
    rows = {}
    for decision in decisions:
        rows.setdefault(decision.decided_by_id, []).append(
            (decision.seconds_spent, not decision.primary_label)
        )
    pace = cin_metrics.coder_pace(rows)
    return {
        coder: {**pace.get(coder, {}), "agreement": agreement.get(coder)}
        for coder in set(pace) | set(agreement)
    }


# ------------------------------------------------------ third on a split


def add_third_coder(cohort, article_id):
    """Give a story a third coder where its first two disagreed.

    In a round that asks for it, a story is read by two coders, and by a
    third only when those two chose differently -- a category against
    another category, or against a rejection. Two who both reject it
    have agreed it is not a story to label, and it gets nobody else.

    The third is an admitted coder who has not seen the story, the one
    with the least outstanding work. Returns the new assignment, or None
    where there is nothing to do. Safe to call again.
    """
    for_round = cohort.round
    if for_round is None or not for_round.third_on_split or for_round.is_practice:
        return None
    decisions = list(
        counted_decisions(cohort.codebook).filter(cohort=cohort, article_id=article_id)
    )
    if len(decisions) != 2 or _agree(*decisions) is not False:
        return None
    on_story = set(
        ClassificationAssignment.objects.filter(
            cohort=cohort, article_id=article_id, is_check=False
        ).values_list("assigned_to_id", flat=True)
    )
    if len(on_story) > 2:
        return None
    standing = standings(cohort.codebook)
    seen = set(
        ClassificationDecision.objects.filter(article_id=article_id).values_list(
            "decided_by_id", flat=True
        )
    )
    free = [
        user
        for user in coders_for(cohort)
        if standing.get(user.id) == CoderStanding.ADMITTED
        and user.id not in on_story
        and user.id not in seen
    ]
    if not free:
        return None
    load = {}
    for user_id in ClassificationAssignment.objects.filter(
        assigned_to__in=free, completed_at__isnull=True
    ).values_list("assigned_to_id", flat=True):
        load[user_id] = load.get(user_id, 0) + 1
    third = min(free, key=lambda user: (load.get(user.id, 0), user.username))
    return ClassificationAssignment.objects.create(
        cohort=cohort, article_id=article_id, assigned_to=third
    )


# ------------------------------------------------------------ check stories

#: How many check stories a coder is judged on, and how many are hidden
#: in their queue each time a batch is opened until they have that many.
CHECK_STORIES = 40
CHECK_STORIES_PER_BATCH = 10


def find_check_stories(version):
    """Record the pilot stories every coder agreed on as check stories.

    From pilot rounds under this codebook: a story is a check story when
    it has all its coders' answers and every one chose the same
    category. Idempotent; returns how many there are.
    """
    pilots = Round.objects.filter(kind=Round.COMPARISON, codebook_version=version)
    base = {pilot.pk: pilot.base_coders for pilot in pilots}
    by_story = {}
    for decision in counted_decisions(version).filter(cohort__round__in=pilots):
        by_story.setdefault((decision.article_id, decision.cohort.round_id), []).append(
            decision.primary_label
        )
    known = set(
        CheckStory.objects.filter(codebook_version=version).values_list(
            "article_id", flat=True
        )
    )
    made = [
        CheckStory(codebook_version=version, article_id=story, answer=labels[0])
        for (story, pilot), labels in by_story.items()
        if story not in known
        and len(labels) >= base[pilot]
        and labels[0]
        and len(set(labels)) == 1
    ]
    CheckStory.objects.bulk_create(made)
    return len(known) + len(made)


def hide_check_stories(cohort, per_coder=CHECK_STORIES_PER_BATCH):
    """Hide check stories among each coder's work in a batch.

    Up to `per_coder` each, until a coder has `CHECK_STORIES` in the
    round, and only stories that coder has never coded: no coder codes a
    story twice, so a pilot coder is given check stories from the part
    of the pilot they were not assigned. A queue is worked in a random
    order, so a check story looks like any other.
    """
    for_round = cohort.round
    if for_round is None or for_round.kind != Round.COLLECTION:
        return 0
    version = cohort.codebook
    pool = list(
        CheckStory.objects.filter(codebook_version=version).values_list(
            "article_id", flat=True
        )
    )
    if not pool:
        return 0
    standing = standings(version)
    made = []
    for user in coders_for(cohort):
        if standing.get(user.id) != CoderStanding.ADMITTED:
            continue
        have = ClassificationAssignment.objects.filter(
            cohort__round=for_round, assigned_to=user, is_check=True
        ).count()
        want = min(per_coder, CHECK_STORIES - have)
        if want <= 0:
            continue
        seen = set(
            ClassificationDecision.objects.filter(decided_by=user).values_list(
                "article_id", flat=True
            )
        ) | set(
            ClassificationAssignment.objects.filter(assigned_to=user).values_list(
                "article_id", flat=True
            )
        )
        fresh = [story for story in pool if story not in seen]
        chooser = random.Random(f"{cohort.pk}-{user.pk}")
        for story in chooser.sample(fresh, min(want, len(fresh))):
            made.append(
                ClassificationAssignment(
                    cohort=cohort, article_id=story, assigned_to=user, is_check=True
                )
            )
    ClassificationAssignment.objects.bulk_create(made)
    return len(made)


def check_figures(for_round):
    """coder id -> (check stories answered, misses) in a round."""
    answers = dict(
        CheckStory.objects.filter(
            codebook_version=for_round.codebook_version
        ).values_list("article_id", "answer")
    )
    out = {}
    for user_id, story, label in ClassificationDecision.objects.filter(
        cohort__round=for_round, is_check=True
    ).values_list("decided_by_id", "article_id", "primary_label"):
        done, misses = out.get(user_id, (0, 0))
        out[user_id] = (done + 1, misses + (label != answers.get(story)))
    return out


def judge_check_stories(for_round, *, by=None):
    """Judge every coder who has reached `CHECK_STORIES` and has not
    been judged, and pause those over the threshold.

    The normal miss rate is the OTHER coders' combined rate, so a coder
    is not compared with themselves. A coder is not judged until the
    others have answered at least `CHECK_STORIES` check stories between
    them: a rate from a handful of answers is not a rate.
    """
    figures = check_figures(for_round)
    judged = set(
        CheckResult.objects.filter(round=for_round).values_list("user_id", flat=True)
    )
    results = []
    for user_id, (done, misses) in figures.items():
        if user_id in judged or done < CHECK_STORIES:
            continue
        others_done = sum(d for u, (d, _) in figures.items() if u != user_id)
        others_missed = sum(x for u, (_, x) in figures.items() if u != user_id)
        if others_done < CHECK_STORIES:
            continue
        normal = others_missed / others_done
        threshold = cin_metrics.pause_threshold(done, normal)
        paused = misses >= threshold
        result = CheckResult.objects.create(
            user_id=user_id,
            round=for_round,
            stories=done,
            misses=misses,
            normal_miss_rate=normal,
            threshold=threshold,
            paused=paused,
        )
        if paused:
            set_standing(
                result.user,
                for_round.codebook_version,
                CoderStanding.PAUSED,
                by=by,
                note=(
                    f"check stories: {misses} missed of {done}; paused at "
                    f"{threshold}, the others missing {normal:.0%}"
                ),
            )
        results.append(result)
    return results


# ------------------------------------------------------- training export

EXPORT_FIELDS = (
    "story_id",
    "source",
    "label",
    "secondary",
    "confidence",
    "weight",
    "secondary_weight",
    "n_coders",
    "codebook_version",
    "round",
    "stratum",
    "inclusion_probability",
    "split",
)


def training_rows(version, *, shown_names=False):
    """One row per story with a label, for the retrain.

    The stories coded under this codebook in pilot and training rounds
    that settled on a label, and the V1 stories that were not in a pilot
    and came out of V1 with one. Never practice answers, check stories,
    a removed coder's answers, rejected stories, or stories left with no
    label.

    `split` is `holdout` for a story held back when it was drawn, and
    `train` otherwise. With `shown_names` the labels are the names the
    codebook shows (Community Life) rather than the stored ones, for a
    retrain that changes the model's label set.
    """
    name = version.shown if shown_names else (lambda label: label)
    decisions = list(
        counted_decisions(version)
        .filter(cohort__round__kind__in=(Round.COMPARISON, Round.COLLECTION))
        .select_related("cohort__round")
    )
    by_story, rounds = {}, {}
    for decision in sorted(decisions, key=lambda d: (d.decided_at, d.pk)):
        by_story.setdefault(decision.article_id, []).append(decision)
        rounds[decision.article_id] = decision.cohort.round
    samples = {
        sample.article_id: sample
        for sample in ClassificationSample.objects.filter(
            article_id__in=list(by_story), cohort__codebook_version=version
        )
    }
    rows = []
    for story, made in by_story.items():
        confidence, label = cin_metrics.settled_label([as_answer(d) for d in made])
        if not label:
            continue
        secondary = next(
            (
                d.secondary_label
                for d in made
                if d.primary_label == label
                and d.secondary_label
                and d.secondary_label != label
            ),
            "",
        )
        sample = samples.get(story)
        rows.append(
            {
                "story_id": story,
                "source": "v1" if ":" in story else "crawler",
                "label": name(label),
                "secondary": name(secondary) if secondary else "",
                "confidence": confidence,
                "weight": 1.0,
                "secondary_weight": 0.5 if secondary else "",
                "n_coders": len(made),
                "codebook_version": version.name,
                "round": rounds[story].name,
                "stratum": sample.stratum if sample else "",
                "inclusion_probability": sample.inclusion_probability if sample else "",
                "split": "holdout" if sample and sample.holdout else "train",
            }
        )

    piloted = set(
        ClassificationSample.objects.filter(
            cohort__round__kind=Round.COMPARISON, cohort__codebook_version=version
        ).values_list("article_id", flat=True)
    )
    coders = {}
    for story_id in V1Decision.objects.values_list("story_id", flat=True):
        coders[story_id] = coders.get(story_id, 0) + 1
    for story in ClassificationStory.objects.filter(
        origin=ClassificationStory.V1,
        v1_outcome__in=(ClassificationStory.LABEL_09, ClassificationStory.LABEL_07),
    ).exclude(id__in=piloted):
        rows.append(
            {
                "story_id": story.id,
                "source": "v1",
                "label": name(story.v1_label),
                "secondary": "",
                "confidence": (
                    0.9 if story.v1_outcome == ClassificationStory.LABEL_09 else 0.7
                ),
                "weight": 1.0,
                "secondary_weight": "",
                "n_coders": coders.get(story.id, 0),
                "codebook_version": "V1",
                "round": "",
                "stratum": "",
                "inclusion_probability": "",
                "split": "train",
            }
        )
    rows.sort(key=lambda row: row["story_id"])
    return rows
