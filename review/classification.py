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
import re

from django.conf import settings
from django.db import models
from lnic_contracts import cin_labels

from accounts.privileges import CLASSIFIER
from review import cin_metrics
from review.geography_icr import CIN, GEOGRAPHY, GEOGRAPHY_STRATA, TASKS

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
    #: What the version is a guide to: CIN categories, or where a story
    #: is (review.geography_icr). Kept apart so the geography guide is
    #: never what a CIN round opens under by default, and admission to
    #: one is never admission to the other.
    task = models.CharField(max_length=10, choices=TASKS, default=CIN)
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
        """The most recently frozen CIN version: what a new cohort is
        opened under unless told otherwise."""
        return cls.objects.filter(task=CIN).first() or cls.v1()

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
    #: The question coders answer: a CIN category, or where the story is
    #: (docs/GEOGRAPHY_ICR_DATADESK.md). Kinds, batches, assignments and
    #: standing work the same for both.
    task = models.CharField(max_length=10, choices=TASKS, default=CIN)
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
    #: Addresses added to the round before it had a batch. A coder is
    #: granted batches, not rounds, so until one exists there is nothing
    #: to grant; they wait here and are granted the first batch opened.
    waiting_coders = models.JSONField(default=list, blank=True)
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

    @property
    def is_geography(self):
        return self.task == GEOGRAPHY


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


class LeadDecision(models.Model):
    """What the lead decided after a pilot missed its conditions.

    A pilot passes on strict alpha over the primary category. Where it
    misses, and agreement with part credit for a second choice is still
    reliable, the lead may keep the definitions anyway. That is a
    judgement, not a calculation, and it has to be explained when the
    work is written up: so it is written down here, with its reason, by
    whom and when, against the recorded figures it was taken on. Never
    changed; a second decision is a second row.
    """

    KEEP = "keep"
    REVISE = "revise"
    CHOICES = [
        (KEEP, "Keep the definitions and go on"),
        (REVISE, "Revise the definitions and repeat the pilot"),
    ]

    round = models.ForeignKey(Round, on_delete=models.PROTECT, related_name="decisions")
    gate = models.ForeignKey(
        GateResult, on_delete=models.PROTECT, related_name="decisions"
    )
    choice = models.CharField(max_length=8, choices=CHOICES)
    reason = models.TextField()
    decided_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-decided_at", "-id"]

    def __str__(self):
        return f"{self.round}: {self.choice}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("a decision is a record; record another instead")
        super().save(*args, **kwargs)


def record_lead_decision(for_round, choice, reason, by=None):
    """Write down the lead's decision on a pilot that missed.

    Against the round's latest recorded evaluation, which has to be a
    fail: a decision about figures nobody wrote down, or about a pilot
    that passed or is unfinished, is not one this records. Raises
    ValueError saying which.
    """
    reason = (reason or "").strip()
    if choice not in dict(LeadDecision.CHOICES):
        raise ValueError("Choose whether to keep the definitions or revise them.")
    if not reason:
        raise ValueError("A decision needs its reason.")
    latest = for_round.gates.first()
    if latest is None:
        raise ValueError("Record an evaluation first: a decision is about its figures.")
    if latest.outcome != GateResult.FAIL:
        raise ValueError(
            "The latest recorded evaluation is not a fail, so there is "
            "nothing for the lead to decide."
        )
    return LeadDecision.objects.create(
        round=for_round, gate=latest, choice=choice, reason=reason, decided_by=by
    )


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
    #: A geography pilot's strata (review.geography_icr) are the
    #: pipeline's kinds of placement, the doubtful ones oversampled.
    STRATA = [
        (s, s)
        for s in (
            CONFIDENT,
            UNCERTAIN,
            RANDOM,
            UNLABELLED,
            CIVIC,
            OTHER,
            CATEGORY,
            *GEOGRAPHY_STRATA,
        )
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


#: Where stored text has no line breaks at all, how many sentences make
#: a paragraph on the page. Four in ten stories in the pool are stored
#: as one unbroken line.
SENTENCES_PER_PARAGRAPH = 3

_TAG = re.compile(r"<[^>]{1,300}>")
#: The end of a sentence: a full stop, question or exclamation mark,
#: any closing quote or bracket, then space and the start of the next.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+(?=[\"“‘(\[]?[A-Z0-9])")


def readable_text(text):
    """A story's text as paragraphs a person can read.

    Presentation only; no word is added, dropped or reordered beyond
    markup. Leftover tags are removed and character entities turned back
    into characters (`<a href=...>`, `&rsquo;`), which is what the
    extraction should have done. Text stored with line breaks keeps
    them. Text stored as one line is broken every
    `SENTENCES_PER_PARAGRAPH` sentences: where the original paragraphs
    fell is not recorded, and a 1,000-word line cannot be read.
    """
    import html

    value = html.unescape(_TAG.sub("", text or "")).replace("\r\n", "\n").strip()
    if not value:
        return ""
    if "\n" in value:
        blocks = [" ".join(block.split()) for block in re.split(r"\n\s*", value)]
        return "\n\n".join(block for block in blocks if block)
    sentences = [part.strip() for part in _SENTENCE_END.split(value) if part.strip()]
    step = SENTENCES_PER_PARAGRAPH
    return "\n\n".join(
        " ".join(sentences[i : i + step]) for i in range(0, len(sentences), step)
    )


#: Text that is the page around a story, not the story: a comment form,
#: a paywall overlay. A story whose stored text carries any of these was
#: not extracted cleanly, and is not drawn. About 170 of 17,600.
FURNITURE = (
    "Watch this discussion",
    "Post a comment as",
    "This page requires Javascript",
    "Recommended for you",
)


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


#: The answer a rejection counts as when kappa is computed: one more
#: category, never equal to a real one.
REJECTED = "(rejected)"


def agreement_report(codebook_version=None):
    """Agreement overall, by category, by cohort and by coder, under ONE
    codebook.

    Answers given under different definitions are never pooled: a rate
    over V1 and V2 answers together is a rate under no codebook at all.
    Defaults to the current version. Practice rounds and removed
    coders are left out (`counted_decisions`).

    Each figure is given as observed agreement, the agreement expected
    by chance, and Cohen's kappa, which is how far observed sits above
    chance. Kappa is the measure the V1 coding was reported in (0.583).
    Its chance term comes from how often each answer was used in the
    pairs scored, and that depends on how the stories were drawn, so
    kappas are comparable between rounds drawn the same way.

    A rejection counts as one more answer: a category against a
    rejection is a mismatch, as it is for percent agreement. Per
    category, the figures are that category against the rest.

    Per coder, the figures are over first pairs (`cin_metrics.first_pair`)
    with the coder as the first rater: a third coder is given only the
    stories two others split on, and over all pairs would look worse
    than they are. "Kappa without" is the kappa of the first pairs the
    coder is not in, the figure to read theirs against.
    """
    version = codebook_version or CodebookVersion.current()
    decisions = list(
        counted_decisions(version).select_related("cohort").order_by("article_id")
    )
    # Scored pairs only: two rejections are not a pair to score.
    pairs = [(a, b) for a, b in _pairs(decisions) if _agree(a, b) is not None]

    def answer(d):
        return d.primary_label or REJECTED

    def pct(value):
        return round(100 * value, 1) if value is not None else None

    def parts(scored):
        out = cin_metrics.kappa_parts(scored)
        return {
            "n": out["n"],
            "observed": pct(out["observed"]),
            "expected": pct(out["expected"]),
            "kappa": round(out["kappa"], 3) if out["kappa"] is not None else None,
        }

    def against_rest(scored, label):
        return parts((x == label, y == label) for x, y in scored)

    def worst_first(row):
        return (row["kappa"] is None, row["kappa"] if row["kappa"] is not None else 0)

    answered = [(answer(a), answer(b)) for a, b in pairs]
    overall = parts(answered)

    # How often each answer was given, the input to every chance term.
    used = {}
    for d in decisions:
        used[answer(d)] = used.get(answer(d), 0) + 1

    by_category = {}
    for a, b in pairs:
        # Attributed to both answers when they differ: a disagreement
        # between Civic information and Civic Life is a fact about both,
        # and charging it only to the first would understate whichever
        # category happens to sort earlier.
        for label in {a.primary_label, b.primary_label} - {""}:
            hit, total = by_category.get(label, (0, 0))
            by_category[label] = (hit + (a.primary_label == b.primary_label), total + 1)

    categories = sorted(
        (
            {
                "label": label,
                "shown": version.shown(label),
                "share": pct(used.get(label, 0) / len(decisions)),
                "pairs": total,
                "rate": pct(hit / total),
                **against_rest(answered, label),
            }
            for label, (hit, total) in by_category.items()
        ),
        key=worst_first,
    )

    cohort_pairs = {}
    for a, b in pairs:
        cohort_pairs.setdefault(a.cohort.number, []).append((answer(a), answer(b)))
    # KRIPPENDORFF'S ALPHA BESIDE KAPPA (2026-11-23): the same measures the
    # round's finish line reads (`_pilot_conditions`), over every answer on
    # a story rather than pairs, so a third coder on a split counts. Strict
    # alpha reads the category alone; weighted alpha gives part credit where
    # one coder's category is the other's secondary, at the label rule's
    # 0.7 against 0.9 (`cin_metrics.weighted_alpha`) -- the agreement the
    # training labels are built on. Kappa stays the figure compared with V1.
    stories = answers_by_story(decisions)

    def alphas(subset, label=None):
        strict = cin_metrics.krippendorff_alpha(subset, label)
        weighted = cin_metrics.weighted_alpha(subset, label)
        return {
            "alpha": round(strict, 3) if strict is not None else None,
            "alpha_weighted": round(weighted, 3) if weighted is not None else None,
        }

    for row in categories:
        row.update(alphas(stories, row["label"]))
    cohort_of = {d.article_id: d.cohort.number for d in decisions}
    cohorts = [
        {
            "number": number,
            **parts(scored),
            **alphas({k: v for k, v in stories.items() if cohort_of[k] == number}),
        }
        for number, scored in sorted(cohort_pairs.items(), reverse=True)
    ]

    def interval(measure):
        out = cin_metrics.alpha_with_interval(stories, measure=measure)
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in out.items()}

    return {
        "version": version,
        "versions": list(CodebookVersion.objects.filter(task=CIN)),
        "pairs": len(pairs),
        "articles_multiply_coded": len({a.article_id for a, _ in pairs}),
        "decisions": len(decisions),
        "overall": overall["observed"],
        "expected": overall["expected"],
        "kappa": overall["kappa"],
        "alpha": interval(cin_metrics.krippendorff_alpha),
        "alpha_weighted": interval(cin_metrics.weighted_alpha),
        "categories": categories,
        "cohorts": cohorts,
        "category_columns": [version.shown(label) for label in CIN_LABELS],
        **_coder_agreement(decisions, version, answer, parts, against_rest, pct),
    }


def _coder_agreement(decisions, version, answer, parts, against_rest, pct):
    """The per-coder half of `agreement_report`: one row per coder, and
    a coder x category grid of kappa and of how often each category was
    chosen."""
    from django.contrib.auth import get_user_model

    by_story = {}
    for d in sorted(decisions, key=lambda d: (d.decided_at, d.pk)):
        by_story.setdefault(d.article_id, []).append(d)
    first = [
        (made[0], made[1])
        for made in by_story.values()
        if len(made) >= 2 and _agree(made[0], made[1]) is not None
    ]
    everyone = [(answer(a), answer(b)) for a, b in first]

    given, answered_by = {}, {}
    for d in decisions:
        given.setdefault(d.decided_by_id, []).append(answer(d))
        answered_by.setdefault(d.decided_by_id, []).append(as_answer(d))
    stories = answers_by_story(decisions)

    def secondary_counts(coders_):
        made = [a for c in coders_ for a in answered_by[c] if a["label"]]
        return sum(bool(a["secondary"]) for a in made), len(made)

    # First pass: coders adding a secondary too often against everyone
    # else, left out of the comparison for the rest (below).
    def rest_of(c):
        return [x for x in answered_by if x != c]

    adds_freely = {
        c
        for c in answered_by
        if cin_metrics.share_outlier(
            *secondary_counts([c]), *secondary_counts(rest_of(c))
        )
        == "high"
    }

    # First pass for category use, the same way: per category, coders who
    # choose it too often against everyone else.
    def use_of(coders_, label):
        made = [x for c in coders_ for x in given[c]]
        return made.count(label), len(made)

    overuse = {
        label: {
            c
            for c in given
            if cin_metrics.share_outlier(
                *use_of([c], label), *use_of([x for x in given if x != c], label)
            )
            == "high"
        }
        for label in CIN_LABELS
    }

    def share(answers, label):
        return pct(answers.count(label) / len(answers)) if answers else None

    names = {
        user.pk: user.get_full_name() or user.username
        for user in get_user_model().objects.filter(pk__in=list(given))
    }
    coders, kappa_grid, share_grid = [], [], []
    for coder in sorted(given, key=lambda c: names.get(c, "").lower()):
        own = [(answer(a), answer(b)) for a, b in first if a.decided_by_id == coder]
        own += [(answer(b), answer(a)) for a, b in first if b.decided_by_id == coder]
        rest = [
            (answer(a), answer(b))
            for a, b in first
            if coder not in (a.decided_by_id, b.decided_by_id)
        ]
        mine = given[coder]
        name = names.get(coder, f"coder {coder}")
        flags = []
        outlier = cin_metrics.kappa_outlier(
            own, rest, min_pairs=cin_metrics.OUTLIER_MIN_PAIRS
        )
        if outlier:
            flags.append("Kappa overall")
        # Weighted alpha over the stories the coder answered, every answer
        # on them, read against the same alpha with the coder's answers
        # taken out of every story: what the agreement is without them.
        theirs = {
            k: v for k, v in stories.items() if any(a["coder"] == coder for a in v)
        }
        without = {k: [a for a in v if a["coder"] != coder] for k, v in stories.items()}
        alpha_own = cin_metrics.weighted_alpha(theirs)
        alpha_without = cin_metrics.weighted_alpha(without)
        # The secondary is optional and weighted alpha credits it, so a
        # coder adding one freely earns more of that credit: their rate of
        # answers with a secondary, against everyone else's, by the
        # category-use rule (2x or under half, p < 0.05, from 20 answers).
        # Everyone else leaves out a coder already found adding them too
        # often: one such coder raised "everyone else" enough to flag a
        # normal coder as adding too few (24.6% against 51.5%, test data).
        cat_mine = [a for a in answered_by[coder] if a["label"]]
        cat_others = [
            a
            for c, v in answered_by.items()
            if c != coder and c not in adds_freely
            for a in v
            if a["label"]
        ]
        sec_mine = sum(bool(a["secondary"]) for a in cat_mine)
        sec_others = sum(bool(a["secondary"]) for a in cat_others)
        sec_flag = cin_metrics.share_outlier(
            sec_mine, len(cat_mine), sec_others, len(cat_others)
        )
        if sec_flag:
            flags.append(
                "Secondary added too " + ("often" if sec_flag == "high" else "rarely")
            )

        kappa_cells, share_cells = [], []
        for label in CIN_LABELS:
            shown = version.shown(label)
            own_bin = [(x == label, y == label) for x, y in own]
            rest_bin = [(x == label, y == label) for x, y in rest]
            # Judged only once enough of the coder's pairs name the
            # category; pairs where neither did say little about it.
            naming = sum(x or y for x, y in own_bin)
            low = naming >= cin_metrics.OUTLIER_MIN_CATEGORY_PAIRS and (
                cin_metrics.kappa_outlier(own_bin, rest_bin, min_pairs=1)
            )
            kappa_cells.append(
                {
                    "kappa": against_rest(own, label)["kappa"],
                    "without": against_rest(rest, label)["kappa"],
                    "flag": low,
                }
            )
            if low:
                flags.append(f"{shown} kappa")
            # Everyone else leaves out a coder already found choosing this
            # category too often, as for secondaries: one over-user raised
            # "everyone else" enough to flag normal coders as under-users.
            # The share shown beneath is the one compared against.
            ref = [
                x
                for c, answers in given.items()
                if c != coder and c not in overuse[label]
                for x in answers
            ]
            used = cin_metrics.share_outlier(
                mine.count(label), len(mine), ref.count(label), len(ref)
            )
            share_cells.append(
                {
                    "coder": share(mine, label),
                    "others": share(ref, label),
                    "flag": used,
                }
            )
            if used:
                often = "often" if used == "high" else "rarely"
                flags.append(f"{shown} chosen too {often}")
        coders.append(
            {
                "name": name,
                "answers": len(mine),
                "rejected": share(mine, REJECTED),
                **parts(own),
                "kappa_without": parts(rest)["kappa"],
                "alpha_weighted": (
                    round(alpha_own, 3) if alpha_own is not None else None
                ),
                "alpha_weighted_without": (
                    round(alpha_without, 3) if alpha_without is not None else None
                ),
                "secondary": pct(sec_mine / len(cat_mine)) if cat_mine else None,
                "secondary_others": (
                    pct(sec_others / len(cat_others)) if cat_others else None
                ),
                "secondary_flag": sec_flag,
                "flag": outlier,
                "flags": flags,
            }
        )
        kappa_grid.append({"name": name, "cells": kappa_cells})
        share_grid.append({"name": name, "cells": share_cells})
    every = [x for answers in given.values() for x in answers]
    return {
        "first_pairs": len(first),
        "coders": coders,
        "coder_kappa": kappa_grid,
        "everyone_kappa": [
            against_rest(everyone, label)["kappa"] for label in CIN_LABELS
        ],
        "coder_share": share_grid,
        "everyone_share": [share(every, label) for label in CIN_LABELS],
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


#: How a cohort is divided between the strata. Equal thirds, and
#: confident/uncertain equal to each other by construction: the whole
#: point of those two is a direct comparison, which an unequal draw
#: blurs. `docs/CLASSIFICATION_REVIEW_QUEUE.md` sets out why.
#:
#: No unlabelled stratum. Every drawn story must carry a model label
#: (`_pool`), because a coder's answer is scored against it; a story the
#: model never labelled cannot be.
STRATUM_SHARE = {
    ClassificationSample.CONFIDENT: 1 / 3,
    ClassificationSample.UNCERTAIN: 1 / 3,
    ClassificationSample.RANDOM: 1 / 3,
}

#: The confidence bands. The middle (0.5-0.7) is deliberately in neither:
#: it is neither claim, and including it blurs the one thing these two
#: strata exist to separate. Those rows stay reachable at random.
CONFIDENT_AT_OR_ABOVE = 0.7
UNCERTAIN_BELOW = 0.5

#: The statuses a story can be drawn in: the ones a local story ends
#: in. `labeled` is a story the model has labelled, `enriched` what
#: becomes of one after that.
#:
#: Not `cleaned` or `local`, which are stages a story passes through: a
#: story drawn there can still become wire, an obituary or not an
#: article after a coder has read it. Not `enrichment_skipped`: those
#: are paywall stubs, a median of 39 words. And not the statuses a story
#: is set aside in (`wire`, `obituary`, `opinion`, `paywall` and the
#: rest), which are not local news stories to label.
CLASSIFIABLE_STATUSES = ("labeled", "enriched")

#: The fewest words a story can have and be drawn. Enrichment checks a
#: story for a paywall or a teaser before it runs; a story that has not
#: been enriched has had no such check, and a status says nothing about
#: how much text there is. The number is from the V1 coding: of the 68
#: answers that rejected a story as too short to evaluate, 66 (97%) were
#: on stories under 100 words. Applied to every story, enriched or not:
#: one in ten enriched Missouri stories is under it too.
MIN_WORDS = 100

#: Missouri is drawn only where a story has been through enrichment:
#: the March 2026 set, about 14,800 stories, which is the part of that
#: dataset that has been fully collected, cleaned and reconciled. Its
#: other 80,000 labelled stories have a model label and nothing more.
#: A dataset not named here is drawn in every classifiable status.
DATASET_STATUSES = {
    "Mizzou-Missouri-State": ("enriched",),
}

#: The datasets stories are drawn from. Vermont is left out: student
#: newspapers, not fully collected or labelled.
DRAW_DATASETS = (
    "Mizzou-Missouri-State",
    "WSU-Washington-State",
    "Penn-State-Lehigh",
)
#: Missouri is most of the stories that can be drawn. Drawn without regard to state, a
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


def _furniture():
    """Matches a story whose text carries page furniture."""
    found = models.Q()
    for phrase in FURNITURE:
        found |= models.Q(text__contains=phrase)
    return found


def _pool():
    """Every crawler story a draw may take: local, finished, long enough
    to read (`MIN_WORDS`), and labelled by the model.

    The model label is required because agreement with the model is
    measured on every round. A finished story can lack one -- enriched
    without passing through the classifier -- and an answer on it
    scores against nothing.

    A status is not the whole of it. The wire check can find syndication
    on a story whose status was never moved to `wire` -- about 1,400 of
    the labelled stories -- so those are left out by what the check
    found, the test `Article.is_wire` makes.
    """
    from explorer.models import Article

    rows = (
        Article.objects.using("crawler")
        .filter(status__in=CLASSIFIABLE_STATUSES, primary_label__isnull=False)
        .exclude(primary_label="")
        .exclude(wire_check_status="wire")
        .exclude(_furniture())
        .extra(
            where=[
                "(wire IS NULL OR wire::text IN ('null', '[]', '{}'))",
                # Words, counted as the queue counts them: runs of
                # anything between whitespace.
                "array_length(regexp_split_to_array("
                "btrim(coalesce(articles.text, ''),"
                " ' ' || chr(9) || chr(10) || chr(13)),"
                " '\\s+'), 1) >= %s",
            ],
            params=[MIN_WORDS],
        )
    )
    # Where none of the named datasets exists -- another installation, a
    # test corpus -- there is nothing to restrict to.
    datasets = draw_dataset_ids()
    if not datasets:
        return rows
    rows = rows.filter(dataset_id__in=list(datasets.values()))
    for slug, statuses in DATASET_STATUSES.items():
        if slug in datasets:
            rows = rows.exclude(
                models.Q(dataset_id=datasets[slug]) & ~models.Q(status__in=statuses)
            )
    return rows


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
        # Kept as a name for cohorts drawn before the pool required a
        # label; nothing is drawn into it now.
        return rows.none()
    # Random draws from everything classifiable, including the middle
    # band and the rows the other strata already cover. That overlap is
    # the point: it is the only stratum whose rate is an unbiased
    # estimate of anything.
    return rows


def _one_batch_at_a_time():
    """Hold every other batch being opened until this one is written.

    A batch takes the next cohort number, read as the highest so far
    plus one. Two opened at once read the same number and the second
    failed on the unique constraint: a server error for whoever pressed
    the button again while a slow draw was still running. The lock is
    released when the transaction ends.
    """
    from django.db import connection

    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [BATCH_LOCK])


#: Any fixed number; it names the lock.
BATCH_LOCK = 20261121


def same_task(rows, task, round_path):
    """Rows whose round asks the same question as `task`.

    A cohort opened before rounds existed has no round and was CIN
    coding, so it counts as CIN.
    """
    if task == GEOGRAPHY:
        return rows.filter(**{f"{round_path}__task": GEOGRAPHY})
    return rows.filter(
        models.Q(**{f"{round_path}__isnull": True})
        | models.Q(**{f"{round_path}__task": CIN})
    )


def draw_cohort(
    size,
    *,
    target_coders=3,
    note="",
    stale_after_days=14,
    codebook_version=None,
    for_round=None,
    shares=None,
    pool=None,
):
    """Open a cohort and draw `size` articles into it.

    `shares` is stratum -> share of the draw, `STRATUM_SHARE` unless
    given. A practice round gives one stratum, random: its stories are
    for learning the definitions on, and a draw that reserves a quarter
    for stories the model has not labelled comes up short where there
    are almost none.

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
    each of their rates depend on the other. Earlier cohorts asking the
    other question do not count: a story placed on the map can still be
    given a CIN category, and the two answers measure different things.

    `pool` is stratum -> queryset of articles, `_stratum_pool` unless
    given; a geography round draws from enriched stories instead.
    """
    from django.db import transaction

    if for_round is not None:
        codebook_version = for_round.codebook_version
        target_coders = for_round.base_coders
    taken = set(
        same_task(
            ClassificationSample.objects.all(),
            for_round.task if for_round is not None else CIN,
            "cohort__round",
        ).values_list("article_id", flat=True)
    )
    pool = pool or _stratum_pool
    with transaction.atomic():
        _one_batch_at_a_time()
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
        shares = shares or STRATUM_SHARE
        # Floors, with the remainder to the last stratum, so the strata
        # sum to `size` (thirds of 8 rounded each way make 9).
        wants = {stratum: int(size * share) for stratum, share in shares.items()}
        if abs(sum(shares.values()) - 1) < 1e-9:
            wants[list(shares)[-1]] += size - sum(wants.values())
        for stratum in shares:
            want = wants[stratum]
            available = pool(stratum).exclude(id__in=taken)
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
        admit_waiting_coders(for_round, cohort)
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

    Nor a story they were given in any other cohort. A pilot repeated
    under revised definitions holds the stories of the one before it,
    and each goes to the coders who have not read it.

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
    # Who has had each story before, in any other cohort, as work or as
    # a check story. Practice is exempt: its stories are nobody's work.
    seen = {}
    if not practice:
        for user_id, article_id in (
            same_task(
                ClassificationAssignment.objects.filter(article_id__in=article_ids),
                cohort.round.task if cohort.round is not None else CIN,
                "cohort__round",
            )
            .exclude(cohort=cohort)
            .values_list("assigned_to_id", "article_id")
        ):
            seen.setdefault(article_id, set()).add(user_id)
    for article_id in article_ids:
        already = existing.get(article_id, set())
        before = seen.get(article_id, ())
        # Rotate the starting point per article so the same pair does not
        # share every record: overlap spread across the group is what lets
        # agreement be read per coder rather than for one pairing.
        start = offset % len(coders)
        order = coders[start:] + coders[:start]
        offset += 1
        for user in order:
            if len(already) >= wanted:
                break
            if user.id in already or user.id in before:
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


def add_coder_to_round(for_round, email, *, invited_by=None):
    """Add somebody to a round by address. Returns "added" where they
    were granted its open batches, "invited" where that waits for their
    first sign-in, and "waiting" where the round has no batch yet and
    they will be granted the first one opened."""
    email = (email or "").strip().lower()
    if "@" not in email:
        raise ValueError("that is not an address")
    cohorts = list(for_round.cohorts.filter(closed_at__isnull=True))
    if not cohorts:
        if email not in for_round.waiting_coders:
            for_round.waiting_coders = [*for_round.waiting_coders, email]
            for_round.save(update_fields=["waiting_coders"])
        return "waiting"
    kinds = {
        invite_coder(email, cohort, invited_by=invited_by)[0] for cohort in cohorts
    }
    return "added" if kinds == {"granted"} else "invited"


def admit_waiting_coders(for_round, cohort):
    """Grant a round's waiting coders the batch that has just opened."""
    if not for_round.waiting_coders:
        return 0
    waiting = list(for_round.waiting_coders)
    for email in waiting:
        invite_coder(email, cohort)
    for_round.waiting_coders = []
    for_round.save(update_fields=["waiting_coders"])
    return len(waiting)


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
    name,
    kind,
    *,
    codebook_version=None,
    question="",
    base_coders=3,
    task=CIN,
    **rules,
):
    if codebook_version is None:
        if task == GEOGRAPHY:
            from review.geography_icr import geography_guide

            codebook_version = geography_guide()
        else:
            codebook_version = CodebookVersion.current()
    return Round.objects.create(
        name=name,
        kind=kind,
        task=task,
        codebook_version=codebook_version,
        question=question,
        base_coders=base_coders,
        third_on_split=bool(rules.get("third_on_split")),
        draw_rule=rules.get("draw_rule") or {},
        finish_rule=rules.get("finish_rule") or {},
    )


#: What a round is opened with where nothing else is said: the
#: procedure's own numbers, so starting one is a name, a kind and the
#: coders.
ROUND_DEFAULTS = {
    "practice": {"base_coders": 1, "stories": 20},
    "comparison": {"base_coders": 2, "stories": 400},
    "collection": {
        "base_coders": 2,
        "third_on_split": True,
        "finish_rule": {"per_category": 250, "holdout": 50},
        "stories": 300,
    },
}


def open_batch(for_round, size=None, note=""):
    """Draw a round's next batch of stories and hand them out.

    What is drawn depends on what the round is for: practice stories at
    random, a pilot's V1 and new stories, a training batch by category.
    `size` is the practice or training batch's stories, or a pilot's
    other V1 stories beside every civic one; left out, it is the
    procedure's number. Returns (cohort or None, level, message).
    """
    size = size or ROUND_DEFAULTS[for_round.kind]["stories"]
    if for_round.kind != Round.COLLECTION and for_round.cohorts.exists():
        # A practice round is one list of stories every coder codes,
        # and a pilot is one draw.
        return (
            None,
            "bad",
            "This round already has its stories. Only a training round "
            "takes more than one batch.",
        )
    if for_round.is_geography:
        from review.geography_icr import open_geography_batch

        return open_geography_batch(for_round, size)
    if for_round.kind == Round.COMPARISON:
        cohort, drawn = draw_pilot(for_round, others=size, new=pilot_new_plan())
        if cohort is None:
            return (
                None,
                "bad",
                "No stories to draw. Load the V1 stories first with "
                "manage.py import_v1_stories.",
            )
        made = assign_cohort(cohort)
        return (
            cohort,
            "good",
            f"{drawn['civic']} civic and {drawn['other']} other V1 stories"
            + (
                ", the ones an earlier pilot held that enough admitted "
                "coders have not read"
                if drawn["repeat"]
                else ""
            )
            + f", and {drawn['new']} new stories; {made} assignments made.",
        )
    if for_round.kind == Round.COLLECTION:
        cohort, plan = draw_training_batch(for_round, size)
        if cohort is None:
            return None, "warn", "Nothing drawn: every category is at its floor."
        made = assign_cohort(cohort)
        find_check_stories(for_round.codebook_version)
        hidden = hide_check_stories(cohort)
        total = sum(row["drawn"] for row in plan)
        return (
            cohort,
            "good" if total >= size else "warn",
            f"{total} stories drawn, {made} assignments made, {hidden} "
            "check stories hidden among them.",
        )
    cohort, drawn = draw_cohort(
        size,
        for_round=for_round,
        note=note,
        shares={ClassificationSample.RANDOM: 1.0},
    )
    made = assign_cohort(cohort)
    total = sum(drawn.values())
    return (
        cohort,
        "good" if total == size else "warn",
        f"{total} of {size} stories drawn, {made} assignments made.",
    )


def start_round(
    name, kind, emails, *, codebook_version=None, by=None, task=CIN, **changes
):
    """Open a round, draw its stories, and put its coders on it.

    One step, because it was six on three pages: open the round, open a
    batch, add each coder, invite the ones who had never signed in,
    admit them, assign. `emails` are the coders' sign-in addresses.
    `changes` override `ROUND_DEFAULTS` (base_coders, third_on_split,
    finish_rule, stories, question).

    Returns (round, [(level, message)]). A coder who cannot be given
    stories yet is named with the reason, not left out silently.
    """
    base = dict(ROUND_DEFAULTS[kind])
    # A geography pilot reads each story twice, and a third time where
    # the two disagree (docs/GEOGRAPHY_ICR_DATADESK.md, G-WP6).
    if task == GEOGRAPHY and kind == Round.COMPARISON:
        base["third_on_split"] = True
    settings_for = {**base, **{k: v for k, v in changes.items() if v}}
    stories = settings_for.pop("stories")
    question = settings_for.pop("question", "")
    created = open_round(
        name,
        kind,
        codebook_version=codebook_version,
        question=question,
        task=task,
        **settings_for,
    )
    notices = []
    added = []
    for email in emails:
        try:
            add_coder_to_round(created, email, invited_by=by)
            added.append(email.strip().lower())
        except ValueError:
            notices.append(("bad", f"{email}: that is not an address. Not added."))
    cohort, level, message = open_batch(created, stories)
    notices.append((level, "Stories: " + message))
    if cohort is not None:
        notices += coder_notices(created)
    return created, notices


def coder_notices(for_round):
    """What stands between each coder on a round and their stories, as
    [(level, message)]: nothing, a first sign-in, or admission."""
    from accounts.models import Invitation

    cohorts = list(for_round.cohorts.filter(closed_at__isnull=True))
    slugs = [cohort.slug for cohort in cohorts]
    waiting = list(
        Invitation.objects.filter(scope__in=slugs, accepted_at__isnull=True)
        .values_list("email", flat=True)
        .distinct()
    )
    notices = []
    if waiting:
        notices.append(
            (
                "warn",
                "Not signed in to datadesk yet: "
                + ", ".join(sorted(waiting))
                + ". Each gets their stories the first time they sign in "
                "with that Google address and open Classification. Nothing "
                "else needs doing.",
            )
        )
    if not for_round.is_practice:
        standing = standings(for_round.codebook_version)
        short = sorted(
            str(user)
            for user in round_coders(for_round)
            if standing.get(user.id) != CoderStanding.ADMITTED
        )
        if short:
            notices.append(
                (
                    "warn",
                    "No stories yet, because they have not finished a "
                    f"practice round under {for_round.codebook_version.name}: "
                    + ", ".join(short)
                    + ". Recording the practice round's review admits them; "
                    "or press admit beside their name on this round.",
                )
            )
    have = (
        ClassificationAssignment.objects.filter(cohort__in=cohorts)
        .values("assigned_to")
        .distinct()
        .count()
    )
    notices.append(("good", f"Coders with stories now: {have}."))
    return notices


def give_a_coder_their_stories(user):
    """Assign stories in any round batch a coder has been granted and
    has nothing from yet. Returns how many assignments were made.

    For the coder who was added before they had ever signed in. The
    grant is made at their first sign-in, and assigning used to wait for
    an admin to notice and press a button. Called when they open the
    queue.
    """
    from accounts.models import DATADESK, Grant

    slugs = Grant.objects.filter(user=user, app=DATADESK, role=CLASSIFIER).values_list(
        "scope", flat=True
    )
    made = 0
    for cohort in ClassificationCohort.objects.filter(
        slug__in=list(slugs), closed_at__isnull=True, round__isnull=False
    ):
        if not ClassificationAssignment.objects.filter(
            cohort=cohort, assigned_to=user
        ).exists():
            made += assign_cohort(cohort)
    return made


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

    # The geography question's answers are its own table.
    if round.is_geography:
        from review.geography_icr import GeographyDecision

        answered = GeographyDecision.objects.filter(cohort__in=cohorts, is_check=False)
    else:
        answered = ClassificationDecision.objects.filter(
            cohort__in=cohorts, is_check=False
        )
    decisions = [d for d in answered if d.decided_by_id not in removed]
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
        _one_batch_at_a_time()
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
    admit_waiting_coders(for_round, cohort)
    return cohort


#: New stories in a pilot, by the model's label: more for the two
#: categories the definitions were rewritten for, and enough of every
#: other that a coder meets all ten. 2 x 55 + 8 x 15 = 230.
PILOT_NEW_PER_CIVIC = 55
PILOT_NEW_PER_OTHER = 15
#: About how many stories a pilot holds. One with no V1 stories left to
#: give is made up to this with new ones, in the same proportions.
PILOT_STORIES = 1000


def pilot_new_plan():
    """label -> how many new stories a pilot draws for it."""
    return {
        label: PILOT_NEW_PER_CIVIC if label in GATE_CATEGORIES else PILOT_NEW_PER_OTHER
        for label in CIN_LABELS
    }


def _new_stories(plan, taken):
    """Samples of crawler stories, `plan[label]` of each by the model's
    label: up to a third from each smaller dataset and the rest from
    Missouri, never one already in `taken`."""
    import math

    datasets = draw_dataset_ids()
    small = [datasets[slug] for slug in SMALL_DATASETS if slug in datasets]
    samples = []
    for label, want in plan.items():
        if not want:
            continue
        pool = _pool().filter(primary_label=label).exclude(id__in=taken)
        got = 0
        parts = [(pool.filter(dataset_id=d), math.ceil(want / 3)) for d in small]
        for rows, limit in parts + [(pool.exclude(dataset_id__in=small), want)]:
            limit = min(limit, want - got)
            if limit <= 0:
                continue
            total = rows.count()
            picked = list(
                rows.order_by("?").values_list(
                    "id", "dataset_id", "primary_label_confidence"
                )[:limit]
            )
            for article_id, dataset_id, confidence in picked:
                taken.add(article_id)
                samples.append(
                    ClassificationSample(
                        article_id=article_id,
                        stratum=ClassificationSample.CATEGORY,
                        inclusion_probability=len(picked) / total if total else 1.0,
                        dataset_id=dataset_id or "",
                        model_label=label,
                        model_confidence=confidence,
                    )
                )
            got += len(picked)
    return samples


def _v1_stories_to_repeat(for_round):
    """The V1 stories of earlier pilots that can be coded again.

    A story can be, while enough admitted coders have not read it to
    give it this round's coders per story. Returns None where no earlier
    pilot held V1 stories: this is the first, and draws its own.
    """
    earlier = (
        ClassificationSample.objects.filter(
            cohort__round__kind=Round.COMPARISON,
            stratum__in=(ClassificationSample.CIVIC, ClassificationSample.OTHER),
        )
        .exclude(cohort__round=for_round)
        .order_by("id")
    )
    used = {}
    for sample in earlier:
        used.setdefault(sample.article_id, sample)
    if not used:
        return None
    admitted = {
        user
        for user, status in standings(for_round.codebook_version).items()
        if status == CoderStanding.ADMITTED
    }
    seen = {}
    for user_id, article_id in ClassificationAssignment.objects.filter(
        article_id__in=list(used)
    ).values_list("assigned_to_id", "article_id"):
        seen.setdefault(article_id, set()).add(user_id)
    here = set(
        ClassificationSample.objects.filter(cohort__round=for_round).values_list(
            "article_id", flat=True
        )
    )
    return [
        ClassificationSample(
            article_id=sample.article_id,
            stratum=sample.stratum,
            inclusion_probability=sample.inclusion_probability,
            dataset_id=sample.dataset_id,
        )
        for story, sample in used.items()
        if story not in here
        and (story not in seen or len(admitted - seen[story]) >= for_round.base_coders)
    ]


def draw_pilot(for_round, *, others=400, new=None, seed=0):
    """The pilot's stories: V1 stories, and new ones.

    The V1 stories are there so a V2 answer is compared with the V1
    answers on the same story: every civic V1 story, and `others` of the
    rest. The stories two V1 coders rejected are left out -- they are
    not stories to label -- and the ones V1 coders never agreed on stay
    in: they are what the old definitions failed on, and leaving them
    out would flatter V1. The civic stories are taken whole. The others
    are a random draw, there so the round reads as ordinary local news
    and no coder has a reason to expect one category; the chance each
    had of being drawn is recorded with it.

    The new stories (`new`, label -> how many, by the model's label;
    `pilot_new_plan()` is the procedure's) are from the datasets the
    model will label, so a pass says something about them, and so a
    pilot is never short of stories.

    A later pilot holds the same V1 stories while there are coders who
    have not read them: admitted coders, enough to give each story the
    round's coders per story. With four coders and two per story that is
    one repeat. After that a pilot is new stories only, about
    `PILOT_STORIES` of them. `assign_cohort`
    keeps every story from the coders who have read it.
    """
    import random

    repeat = _v1_stories_to_repeat(for_round)
    if repeat is not None:
        samples = repeat
    else:
        taken = set(ClassificationSample.objects.values_list("article_id", flat=True))
        usable = ClassificationStory.objects.filter(
            origin=ClassificationStory.V1
        ).exclude(v1_outcome=ClassificationStory.REJECTED)
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
    strata = [sample.stratum for sample in samples]
    drawn = {
        "civic": strata.count(ClassificationSample.CIVIC),
        "other": strata.count(ClassificationSample.OTHER),
        "new": 0,
        "repeat": repeat is not None,
    }
    if new:
        if repeat is not None and not samples:
            scale = PILOT_STORIES / sum(new.values())
            new = {label: round(count * scale) for label, count in new.items()}
        taken = set(ClassificationSample.objects.values_list("article_id", flat=True))
        fresh = _new_stories(new, taken)
        drawn["new"] = len(fresh)
        samples += fresh
    if not samples:
        return None, drawn
    note = "pilot: V1 stories" if not drawn["new"] else "pilot: V1 and new stories"
    return _open_batch(for_round, samples, note), drawn


def training_counts(for_round):
    """label -> stories that count toward a training round's floor.

    V1 records count first, and only those with a label: the label V2
    coders agreed on where the story was in a pilot under this codebook,
    else the label V1 coders agreed on. A V1 story that was rejected, or
    has no agreed label, is not training data and is not counted. Then
    the new stories of such a pilot, and the round's own, that two coders
    agreed on.
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

    # The new stories of a pilot under this codebook, and the round's own.
    v1_ids = set(
        ClassificationStory.objects.filter(origin=ClassificationStory.V1).values_list(
            "id", flat=True
        )
    )
    ours = (piloted - v1_ids) | set(
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

#: The reliability the pilot has to show: Krippendorff's alpha of 0.80,
#: the usual standard in content analysis, over every coder on a story,
#: for all stories and for each of the two civic categories against the
#: rest. Chance agreement across the ten categories is about 0.15, from
#: the V1 coding (64.5% raw agreement at kappa 0.583), so 0.80 is about
#: 83% first-pair agreement. That figure is the plain reading shown
#: beside alpha. A training round is watched against the same alpha,
#: read from the first two coders on each story: a third exists there
#: only on a split, and alpha over every coder would measure the rule
#: that added them.
RELIABILITY_TARGET = 0.80
CHANCE_AGREEMENT = 0.15
GATE_FIRST_PAIR = round(
    cin_metrics.raw_agreement_for_alpha(RELIABILITY_TARGET, CHANCE_AGREEMENT), 2
)
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


def _reading(name, population, measured, before=None):
    """A figure shown beside the conditions. It passes or fails nothing."""
    return {
        "name": name,
        "population": population,
        "value": measured.get("rate"),
        "low": measured.get("low"),
        "high": measured.get("high"),
        "n": measured.get("n", 0),
        "v1": before,
    }


def round_answers(for_round):
    """story id -> counted answers in a round, in the order given."""
    return answers_by_story(
        counted_decisions(for_round.codebook_version).filter(cohort__round=for_round)
    )


def _pilot_conditions(for_round, stories):
    """What a pilot has to reach, and the figures shown beside it.

    The conditions are Krippendorff's alpha, at `RELIABILITY_TARGET`:
    over every story, and for each of the two civic categories against
    every other answer. Alpha is over every coder on a story, and is
    computed on the pilot as drawn. The pilot holds every civic V1 story
    and only some of the others, so it is harder than the V1 stories as
    a whole, and the figure errs low.

    The readings are first-pair agreement, the share of stories on which
    the first two coders chose the same category: by group, by civic
    category, and over all stories with the civic ones counted at their
    share of the V1 stories kept. They decide nothing. Every figure is
    shown against what V1 coders did on the same stories.

    Returns (conditions, readings, groups, parts): `parts` is alpha on
    the V1 stories alone, against V1, and on the new stories alone.
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

    def v1_alpha(label=None):
        return cin_metrics.krippendorff_alpha(before, label) if before else None

    conditions = [
        _condition(
            "All stories",
            "every story in the round, every coder",
            cin_metrics.alpha_with_interval(stories),
            RELIABILITY_TARGET,
        )
    ]
    for label in GATE_CATEGORIES:
        conditions.append(
            _condition(
                version.shown(label),
                "the category against every other answer, every coder",
                cin_metrics.alpha_with_interval(stories, label),
                RELIABILITY_TARGET,
            )
        )

    # Alpha with part credit where one coder's category is the other's
    # secondary: the agreement the label rule builds training labels on.
    # It decides nothing. Where the strict figure misses and this one
    # does not, coders saw the same needs in a story and ranked them
    # differently, and whether to keep the definitions is the lead's
    # decision, to be reported as one.
    weighted = [
        _reading(
            "All stories",
            "every story in the round, every coder",
            cin_metrics.alpha_with_interval(
                stories, measure=cin_metrics.weighted_alpha
            ),
        )
    ] + [
        _reading(
            version.shown(label),
            "the category against every other answer, every coder",
            cin_metrics.alpha_with_interval(
                stories, label, measure=cin_metrics.weighted_alpha
            ),
        )
        for label in GATE_CATEGORIES
    ]

    # The same figures on the V1 stories alone, against what V1 coders
    # did on them, and on the new stories alone. A pilot passes on all
    # of its stories; these say where the figure came from.
    v1_ids = {
        story
        for story, stratum in strata.items()
        if stratum in (ClassificationSample.CIVIC, ClassificationSample.OTHER)
    }
    old = {k: v for k, v in stories.items() if k in v1_ids}
    fresh = {k: v for k, v in stories.items() if k not in v1_ids}
    parts = {
        "v1_stories": [],
        "new_stories": [],
        "weighted": weighted,
        "weighted_reliable": all(
            row["value"] is not None and row["value"] >= RELIABILITY_TARGET
            for row in weighted
        ),
        "crossover_credit": cin_metrics.CROSSOVER / cin_metrics.FULL_MATCH,
    }
    if v1_ids:
        size = f"{len(v1_ids)} V1 stories"
        parts["v1_stories"] = [
            _reading(
                "All V1 stories", size, cin_metrics.alpha_with_interval(old), v1_alpha()
            )
        ] + [
            _reading(
                version.shown(label),
                f"{size}, the category against every other answer",
                cin_metrics.alpha_with_interval(old, label),
                v1_alpha(label),
            )
            for label in GATE_CATEGORIES
        ]
    if len(strata) > len(v1_ids):
        size = f"{len(strata) - len(v1_ids)} new stories"
        parts["new_stories"] = [
            _reading("All new stories", size, cin_metrics.alpha_with_interval(fresh))
        ] + [
            _reading(
                version.shown(label),
                f"{size}, the category against every other answer",
                cin_metrics.alpha_with_interval(fresh, label),
            )
            for label in GATE_CATEGORIES
        ]

    def v1_rate(subset, label=None):
        rate = cin_metrics.first_pair_agreement(subset, label)["rate"]
        return rate if subset else None

    readings = []
    if ClassificationSample.CIVIC in groups:
        now, then, size = groups[ClassificationSample.CIVIC]
        readings.append(
            _reading(
                "The civic stories",
                f"{size} V1 stories a V1 coder put in either civic category",
                cin_metrics.first_pair_agreement(now),
                v1_rate(then),
            )
        )
    for label in GATE_CATEGORIES:
        readings.append(
            _reading(
                version.shown(label),
                "V1 stories either of the first two coders put in it",
                cin_metrics.first_pair_agreement(old, label),
                v1_rate(before, label),
            )
        )
    if ClassificationSample.OTHER in groups:
        now, then, size = groups[ClassificationSample.OTHER]
        readings.append(
            _reading(
                "The other stories",
                f"{size} other V1 stories",
                cin_metrics.first_pair_agreement(now),
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
        overall = cin_metrics.first_pair_agreement(old)
        then = v1_rate(before)
        population = "every V1 story in the round"
    readings.append(_reading("All V1 stories", population, overall, then))
    if fresh or len(strata) > len(v1_ids):
        readings.append(
            _reading(
                "The new stories",
                f"{len(strata) - len(v1_ids)} new stories",
                cin_metrics.first_pair_agreement(fresh),
            )
        )
    return conditions, readings, groups, parts


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
        conditions, readings, groups, parts = _pilot_conditions(for_round, stories)
        labelled = sum(
            1 for answers in stories.values() if cin_metrics.settled_label(answers)[1]
        )
        coded = sum(1 for answers in stories.values() if len(answers) >= 2)
        measures = {
            "first_pair": readings,
            **parts,
            "first_pair_reading": GATE_FIRST_PAIR,
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
            # The same measure the pilot passes on, read from the first
            # two coders: a third exists here only on a split, and alpha
            # over every coder would measure the rule that added them.
            "alpha": cin_metrics.alpha_with_interval(
                {story: answers[:2] for story, answers in stories.items()}
            ),
            "alpha_target": RELIABILITY_TARGET,
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

    if not for_round.is_practice:
        measures["secondary_use"] = cin_metrics.secondary_use(stories)
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
    seen = set(
        ClassificationDecision.objects.filter(article_id=article_id).values_list(
            "decided_by_id", flat=True
        )
    )
    return assign_third(cohort, article_id, seen)


def assign_third(cohort, article_id, seen):
    """Assign a story to one more coder: admitted, not already on it, not
    among `seen` (who have answered it anywhere), the one with the least
    outstanding work. Returns the assignment, or None where the story
    already has three or nobody is free.

    Shared by the CIN and geography questions; each decides for itself
    when a story's first two answers disagree.
    """
    on_story = set(
        ClassificationAssignment.objects.filter(
            cohort=cohort, article_id=article_id, is_check=False
        ).values_list("assigned_to_id", flat=True)
    )
    if len(on_story) > 2:
        return None
    standing = standings(cohort.codebook)
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
