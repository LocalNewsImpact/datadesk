"""What the discovery queue asks about, and how much of it.

The subject is a candidate link, not an article: no body, no byline, no
capture -- a URL, its publisher, and what the verification recorded.

THREE STRATA, FOR TWO DIFFERENT QUESTIONS
-----------------------------------------
A doubt-ranked queue finds errors and can never say how many there are:
it is drawn from rows a signal already suspects. A random sample says how
many and finds almost none. Both are wanted, so both are here, and each
row carries which stratum drew it.

**Doubtful** -- the model could not call it. Reviewed in full rather than
sampled: 746 rows in March, and every one is a URL the classifier scored
at the boundary from either side. There is no cheaper way to buy that.

**Overruled** -- the model said story and something rejected it anyway.
12,464 rows in March, all with a *positive* margin, so none of them are
the model's own judgement: storysniffer applies whitelist and blacklist
overrides after predicting. A sample answers whether the override is
doing the right job -- on the evidence of the URLs it catches
(`/news/nation/`, `world_news`, `cnn-spanish`) it may be acting as a
local-news filter rather than a story classifier, which is a different
job than the one it is being scored on.

**Sample** -- drawn uniformly across the whole cohort, including rows the
other two strata also hold. This is the only stratum that can state a
confidence level for a new model, and it only works if it is not
restricted to what the others left over. 400 rows gives +/-5% at 95%,
which is the right precision for error rates in the 1-10% range; +/-3%
costs 670 more rows to sharpen an estimate that moves as soon as the
model changes.

WHY NOT `predict_proba`
-----------------------
It saturates: 97.5% of URLs score exactly 0.0 or 1.0, so "nearly said
yes" has no meaning in it. The margin from `predict_log_proba` orders
URLs and is what the bands below are cut on. Its magnitudes run to
thousands and mean nothing on their own -- see `UrlVerification`.
"""

from django.db.models import Q

from review import kernel

#: Keys, so a template and a view cannot drift on a spelling.
DOUBTFUL = "doubtful"
OVERRULED = "overruled"
SAMPLE = "sample"

#: |margin| within this is the band the model could not call. Cut at 25
#: because March holds 746 rows inside it and 2,860 inside 100 -- the
#: wider band is mostly one-sided and stops being "doubtful".
DOUBTFUL_MARGIN = 25.0

#: Above this a positive margin is decisive, so a rejection at it was an
#: override rather than the model.
DECISIVE_MARGIN = 100.0

#: How many rows each stratum contributes. Doubtful is None -- it is
#: reviewed in full, not sampled.
STRATUM_SIZE = {
    DOUBTFUL: None,
    OVERRULED: 300,
    SAMPLE: 400,
}

STRATA = (
    (
        DOUBTFUL,
        "Doubtful",
        "The model could not call it, from either side. Reviewed in full.",
    ),
    (
        OVERRULED,
        "Overruled",
        "The model said story; a rule rejected it anyway.",
    ),
    (
        SAMPLE,
        "Random sample",
        "Drawn uniformly, so the error rate has a confidence interval.",
    ),
)

STRATUM_LABELS = {key: label for key, label, _ in STRATA}


def predicate(stratum):
    """The rows a stratum covers, before any sampling.

    `SAMPLE` deliberately has no predicate: restricting it to what the
    other strata left over would destroy the only thing it is for.
    """
    if stratum == DOUBTFUL:
        return Q(
            verification_confidence__gte=-DOUBTFUL_MARGIN,
            verification_confidence__lte=DOUBTFUL_MARGIN,
        )
    if stratum == OVERRULED:
        return Q(
            verification_confidence__gt=DECISIVE_MARGIN,
            storysniffer_result=False,
        )
    return Q()


def in_cohort(qs, start, end):
    """The links discovered in a window.

    Grouped on `candidate_links.discovered_at` and not on an article's
    publish date, because a row rejected before extraction has no
    article. Both bounds are applied to the one column so the window
    cannot be satisfied by two different rows.
    """
    return qs.filter(
        candidate_link__discovered_at__gte=start,
        candidate_link__discovered_at__lt=end,
    )


def drawn(qs, stratum):
    """A stratum's rows, in a stable order, capped at its size.

    Ordered by a hash of the id rather than at random, so that paging
    through a stratum shows each row once and the same 400 rows are drawn
    today and tomorrow. A sample that reshuffles on every request cannot
    be a held-out set.
    """
    ordered = qs.extra(select={"_draw": "md5(url_verifications.id)"}).order_by("_draw")
    size = STRATUM_SIZE.get(stratum)
    return ordered[:size] if size else ordered


def inclusion_probability(stratum, population):
    """The chance a row in this stratum had of being shown.

    Recorded with the label, because a rate measured over rows that were
    not equally likely to be drawn is not an error rate. Full review is
    1.0; a capped stratum is its size over its population.
    """
    size = STRATUM_SIZE.get(stratum)
    if not size or not population:
        return 1.0
    return min(1.0, size / population)


# --------------------------------------------------------------------
# The queue: two verbs, and a qualifier carrying the part worth counting
# --------------------------------------------------------------------

DISCOVERY_QUEUE_KEY = "discovery"

IT_IS_A_STORY = "story"
NOT_A_STORY = "not_story"

#: What the thing actually is, answered alongside either verb. The verb
#: says the call was wrong; this says what the right answer was, and a
#: count of "section index" against a rule or a publisher is what a fix
#: is built from.
#:
#: `section index` and `tag or author page` are kept apart from `story`
#: deliberately: extraction has produced article rows for /profile/ pages
#: titled with the paper's own name, so "an article row exists" is not a
#: usable label and a model trained on the two conflated learns the wrong
#: boundary.
WHAT_IT_IS = (
    ("story", "Story"),
    ("section_index", "Section index"),
    ("tag_or_author", "Tag or author page"),
    ("video", "Video"),
    ("gallery", "Photo gallery"),
    ("event", "Event listing"),
    ("obituary_index", "Obituary listing page"),
    ("account", "Subscribe or account page"),
    ("homepage", "Homepage"),
    ("other", "Other"),
)


def apply_discovery(verification, verb, value, user):
    """Carry out one discovery decision and say what it wrote.

    "It is a story" is the console's only write to `candidate_links`, and
    it writes one column. "Not a story" writes nothing on the crawler:
    the status already excludes the row, and recording the decision is
    what stops the queue asking again.

    The `ReviewDecision` is written by `review/submit.py`, which every
    queue shares -- this returns the payload it stores. Creating one here
    as well produced two rows for one answer, differing only in the
    question they were keyed on.

    `url_verifications.human_label` is deliberately not written. Datadesk
    does not create rows in the crawler's tables (SCOPE.md 2.5), and a
    label kept in two places is a label that can disagree with itself.
    """
    from review.services import audited_update

    # The stratum and its draw probability are annotated onto the row by
    # the view, from the hidden field the form carried. They cannot be
    # recomputed here: `doubtful` and `overruled` follow from the margin,
    # but membership of the random sample does not follow from anything
    # about the row -- that is what makes it a random sample.
    stratum = getattr(verification, "_stratum", "")
    probability = getattr(verification, "_probability", 1.0)

    link = verification.candidate_link
    before = getattr(link, "status", "") or ""
    after = before

    if verb.name == IT_IS_A_STORY and link is not None:
        audited_update(
            user,
            [link],
            {"status": "discovered"},
            action="discovery:restore",
            reason="Reviewer says this URL is a story",
        )
        after = "discovered"

    return {
        "label": (verification.url or "")[:300],
        "before": before,
        "after": after,
        # Everything the labelled set needs to be usable for training.
        # The stratum and the probability are not decoration: a rate
        # measured over rows that were not equally likely to be drawn is
        # not an error rate, and without them the doubt-ranked labels and
        # the random ones cannot be told apart afterwards.
        "wrote": {
            "what_it_is": value or "",
            "stratum": stratum,
            "inclusion_probability": probability,
            "storysniffer_result": verification.storysniffer_result,
            "margin": verification.verification_confidence,
            "recorded_status": verification.new_status,
        },
        "reason": "",
    }


def _verbs_for(verification):
    """Both verbs on every row.

    Unlike the extraction queue there is no per-row narrowing: a link
    either is a story or is not, and nothing about its recorded status
    makes one of those two answers unavailable.
    """
    return DISCOVERY_QUEUE.verbs


DISCOVERY_QUEUE = kernel.register(
    kernel.Queue(
        key=DISCOVERY_QUEUE_KEY,
        subject_type="candidate_link",
        verbs=(
            kernel.Verb(
                name=IT_IS_A_STORY,
                label="It is a story",
                sublabel="Back to the pipeline — it gets fetched on the next run",
                past="restored",
                tone="fix",
                takes_value=True,
                values=WHAT_IT_IS,
            ),
            kernel.Verb(
                name=NOT_A_STORY,
                label="Not a story",
                sublabel="The status stands; the queue stops asking",
                past="confirmed",
                tone="reject",
                takes_value=True,
                values=WHAT_IT_IS,
            ),
        ),
        apply=apply_discovery,
    )
)
