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
from lnic_contracts import cin_labels

from accounts.privileges import CLASSIFIER

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


# ------------------------------------------------------------- the reports
#
# What the CIN admin section answers. Kept here rather than in the view
# because the agreement arithmetic is the substance -- a view should not
# be where "do two coders agree" is decided.


def _pairs(decisions):
    """Every unordered pair of decisions on the same article."""
    from itertools import combinations

    by_article = {}
    for d in decisions:
        by_article.setdefault(d.article_id, []).append(d)
    for made in by_article.values():
        yield from combinations(made, 2)


def _agree(a, b):
    """Whether two coders said the same thing.

    Exact agreement on the primary label. Not set overlap: the primary
    is what the model is trained on, and a pair matching only on
    secondaries has not agreed about what the story is.

    A rejection is a real answer, so two coders who both said "not
    local" agree. That is the honest reading -- they reached the same
    conclusion about the record -- and it is also the one that keeps a
    cohort of unreadable articles from scoring as total disagreement.
    """
    if a.primary_label or b.primary_label:
        return a.primary_label == b.primary_label
    return a.reject_reason == b.reject_reason


def agreement_report():
    """Agreement overall, by dataset and by category.

    Percent agreement, and not kappa. Kappa needs a chance-agreement
    term over a fixed category distribution, and this queue oversamples
    low-incidence categories deliberately -- so the distribution is a
    sampling choice, not a property of the corpus, and a kappa computed
    against it would move when the sampling changed rather than when
    the coding did. The historical kappa (0.583) was computed once,
    over a fixed set, and is reported in the codebook annotations
    instead.
    """
    decisions = list(
        ClassificationDecision.objects.select_related("cohort").order_by("article_id")
    )
    pairs = list(_pairs(decisions))

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
        "pairs": len(pairs),
        "articles_multiply_coded": len({a.article_id for a, _ in pairs}),
        "decisions": len(decisions),
        "overall": rate(agreed, len(pairs)),
        "categories": sorted(
            (
                {"label": label, "rate": rate(hit, total), "pairs": total}
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
CLASSIFIABLE_STATUSES = ("cleaned", "local")


def _stratum_pool(stratum):
    """The articles a stratum may draw from, as an unordered queryset."""
    from explorer.models import Article

    rows = Article.objects.using("crawler").filter(status__in=CLASSIFIABLE_STATUSES)
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


def draw_cohort(size, *, target_coders=3, note="", stale_after_days=14):
    """Open a cohort and draw `size` articles into it.

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
    """
    from django.db import transaction

    coders = coders_for(cohort)
    if not coders:
        return 0

    existing = {}
    for user_id, article_id in ClassificationAssignment.objects.filter(
        cohort=cohort
    ).values_list("assigned_to_id", "article_id"):
        existing.setdefault(article_id, set()).add(user_id)

    wanted = min(cohort.target_coders, len(coders))
    made, offset = [], 0
    article_ids = list(
        ClassificationSample.objects.filter(cohort=cohort)
        .order_by("id")
        .values_list("article_id", flat=True)
    )
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
