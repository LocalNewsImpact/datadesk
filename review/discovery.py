"""What the discovery queue asks about, and how much of it.

The subject is a candidate link, not an article: no body, no byline, no
capture -- a URL, its publisher, and what the verification recorded.

FIVE STRATA, FOR FOUR DIFFERENT QUESTIONS
-----------------------------------------
A doubt-ranked queue finds errors and can never say how many there are:
it is drawn from rows a signal already suspects. A random sample says how
many and finds almost none. Both are wanted, so both are here, and each
row carries which stratum drew it.

The third question is not about error at all. Some links have never been
judged by anything -- they were found, and nothing ruled on them -- so
there is no verdict to second-guess and a reviewer answering is making
the first decision rather than reviewing one.

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

**Never judged** -- nothing ruled on these. `candidate_links.status` is
`discovered`: the link was found, and neither the URL rules nor the model
has been run over it since. The crawler scores them on request
(`backfill-verifications --unjudged`) and marks each row a `prescore`
rather than a backfill, because there is no decision to backfill. 410
were written on 2026-09-14, of which 380 fall outside every doubt-ranked
band on margin alone -- without a stratum of their own only the random
sample would ever have reached them, which for 410 rows is a handful.
Reviewed in full: each one is a link waiting on a decision, not a sample
of anything.

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

import bisect
from datetime import UTC

from django.core.cache import cache
from django.db.models import Q, TextField, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce
from django.db.models.lookups import In
from lnic_contracts import discovery_verdict

from review import kernel

#: What the pipeline treats as "this is an article we keep".
#: What the pipeline concluded, grouped by what it MEANT rather than by
#: the string it wrote. Three different outcomes were reading as one:
#: `wire` sat in the same bucket as `not_article`, so a URL the pipeline
#: accepted as a story, fetched, extracted and then recorded as wire was
#: reported as one it "dropped". The row said `wire` in one column and
#: "the pipeline dropped it anyway" in the next.
#:
#: kept          local news, which is what the analysis keeps.
#: filtered_out  it IS a story, and the analysis does not keep it. Not a
#:               rejection: it was fetched and extracted, and that is how
#:               the pipeline found out. Wire, obituaries, opinion and
#:               weather are all in here -- this is not a wire-only case.
#:
#: Named for what the analysis did with the row, not for a property of
#: the row: an obituary IS local, it is simply not kept. An earlier name
#: for this was `filed`, which meant nothing to anybody.
#: rejected  it is not a story. The only real disagreement with a model
#:           that says it is.
#: unresolved  nothing has decided yet.
KEPT_STATUSES = frozenset({"article", "extracted", "cleaned", "local"})
FILTERED_OUT_STATUSES = frozenset({"wire", "obituary", "opinion", "weather"})
REJECTED_STATUSES = frozenset({"not_article", "404", "skipped"})
UNRESOLVED_STATUSES = frozenset({"discovered", "sampled_out"})

#: Keys, so a template and a view cannot drift on a spelling.
DOUBTFUL = "doubtful"
OVERRULED = "overruled"
NEVER_JUDGED = "never_judged"
INGESTED = "ingested"
SAMPLE = "sample"

#: |margin| within this is the band the model could not call. Cut at 25
#: because March holds 746 rows inside it and 2,860 inside 100 -- the
#: wider band is mostly one-sided and stops being "doubtful".
DOUBTFUL_MARGIN = 25.0

#: Above this a positive margin is decisive, so a rejection at it was an
#: override rather than the model.
DECISIVE_MARGIN = 100.0

#: The mechanisms that mean STORYSNIFFER ITSELF ANSWERED.
#:
#: `_decided_by` in the crawler names four: `wire` (the wire-service URL
#: filter), `pattern:<type>` (the URL pattern rules, including the
#: asset-extension check), `sniffer` (guess() returned true) and
#: `default` (nothing filtered and guess() returned false). Only the last
#: two are the model. The first two return BEFORE `sniffer.guess()` is
#: called and write `storysniffer_result = False` on their way out, so
#: the column holds a rule's answer in the model's field.
MODEL_DECIDED = ("sniffer", "default")

#: Where the mechanism is written, which depends on which path wrote the
#: row. The live verification path puts it in `decided_by`. The backfill
#: puts the literal string "backfill" there -- it is recording that the
#: original decision's mechanism was never captured -- and puts the
#: rescore's mechanism in `rescored_by`. Reading either key alone is
#: wrong on half the table, and today it is wrong on ALL of it: every one
#: of the 245,473 rows is a backfill, because discovery has not run since
#: 2026-08-12.
#: What the crawler writes on a row it scored for the FIRST time. A link
#: at `discovered` has no decision to rescore -- it was found, and
#: nothing has judged it since -- so the crawler marks the row a
#: `prescore` rather than a backfill and gives it this verdict kind.
#:
#: They are their own stratum because the question is different. Every
#: other stratum asks "was the pipeline right about this". These have no
#: pipeline verdict: the question is "is this a story", and a reviewer
#: answering it is making the first decision rather than reviewing one.
NEVER_JUDGED_KIND = "never_judged"

#: What the crawler writes on a row for an INGESTED URL -- one a person
#: supplied as part of a chosen set (`candidate_links.is_curated`). Ingested
#: URLs skip verification so no rule or model removes one; the crawler's
#: `check-ingested` now runs the URL rules and storysniffer over them anyway
#: and records the answer without acting on it. The link is still fetched.
#:
#: Their own stratum, for the same reason as a first score: there is no
#: pipeline verdict to second-guess. A person chose the URL, and the question
#: is whether it belongs in the set. Only the doubted ones are asked about --
#: `flagged` is set when a URL rule rejects it, the URL wire filter matches,
#: or storysniffer says it is not a story. On WSU that is 27 of 2,681.
INGESTED_KIND = "ingested"

VERDICT_KIND = KeyTextTransform("verdict_kind", "meta")
FLAGGED = KeyTextTransform("flagged", "meta")

MECHANISM = Coalesce(
    KeyTextTransform("rescored_by", "meta"),
    KeyTextTransform("decided_by", "meta"),
)

#: How many rows each stratum contributes. Doubtful is None -- it is
#: reviewed in full, not sampled.
STRATUM_SIZE = {
    DOUBTFUL: None,
    OVERRULED: 300,
    # Reviewed in full, like the doubtful band. These are not a sample of
    # anything -- each one is a link waiting on a decision that has never
    # been made, and a capped draw would leave the rest waiting.
    NEVER_JUDGED: None,
    # Reviewed in full: each is a supplied URL something doubts, and there
    # are few of them.
    INGESTED: None,
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
        "Model disagrees",
        "Storysniffer scores it a story; the pipeline did not keep it. "
        "A URL rule rejecting it is not a disagreement and is not here.",
    ),
    (
        NEVER_JUDGED,
        "Never judged",
        "Nothing has ruled on these. Scored for the first time, and "
        "waiting on a decision rather than a second opinion.",
    ),
    (
        INGESTED,
        "Supplied, doubted",
        "A URL someone supplied that the URL rules or storysniffer doubt. "
        "It was still fetched; does it belong in the set?",
    ),
    (
        SAMPLE,
        "Random sample",
        "Drawn uniformly, so the error rate has a confidence interval.",
    ),
)

STRATUM_LABELS = {key: label for key, label, _ in STRATA}


def what_it_is_labels():
    """{value: label} across both lists, for anything that needs the map
    rather than the rendered options."""
    return {
        choice["value"]: choice["label"] for choice in STORY_KINDS + NOT_STORY_KINDS
    }


def predicate(stratum):
    """The rows a stratum covers, before any sampling.

    `SAMPLE` deliberately has no predicate: restricting it to what the
    other strata left over would destroy the only thing it is for.
    """
    # A FIRST SCORE IS NOT A SECOND OPINION, so it is kept out of the
    # strata that ask whether the pipeline was right. 30 of the 410
    # written on 2026-09-14 land inside the doubtful band on margin
    # alone, and they would have been asked about twice -- once with a
    # question that has no answer for them.
    #
    # COALESCED BECAUSE SQL IS THREE-VALUED. Most rows have no
    # `verdict_kind` at all, so `->>` gives NULL, `NULL IN (...)` is NULL
    # rather than false, and `NOT NULL` is NULL -- which filters the row
    # out. Negating a key that is usually absent emptied every stratum it
    # was added to: 20 tests failed on it, all of them rows with no
    # `verdict_kind`. An empty string is a value the negation can be
    # true about.
    #
    # `output_field` because `Coalesce` refuses to guess across a
    # TextField and the CharField a bare `Value("")` resolves to. It
    # raises only when the expression is compiled, so the direct
    # predicate tests passed and the queue itself returned a 500.
    not_a_first_score = ~Q(
        In(
            Coalesce(VERDICT_KIND, Value(""), output_field=TextField()),
            # An ingested URL has no pipeline verdict either: a person
            # chose it. Its own stratum asks about it.
            (NEVER_JUDGED_KIND, INGESTED_KIND),
        )
    )

    if stratum == NEVER_JUDGED:
        return Q(In(VERDICT_KIND, (NEVER_JUDGED_KIND,)))
    if stratum == INGESTED:
        # `->>` of a JSON true is the text 'true'. Unflagged rows are
        # checked-and-fine and are not asked about.
        return Q(In(VERDICT_KIND, (INGESTED_KIND,))) & Q(In(FLAGGED, ("true",)))
    if stratum == DOUBTFUL:
        return (
            Q(
                verification_confidence__gte=-DOUBTFUL_MARGIN,
                verification_confidence__lte=DOUBTFUL_MARGIN,
            )
            & not_a_first_score
        )
    if stratum == OVERRULED:
        # THE MECHANISM HAS TO BE THE MODEL, or this stratum is not what
        # its name says. Measured against production 2026-09-14: 69,514
        # rows sit above the decisive margin with `storysniffer_result`
        # false, and ZERO of them were decided by storysniffer. Every one
        # is a wire hit or a URL pattern -- `/world/`, `/cnn/`, a feed, a
        # photo gallery -- and a reviewer reading the stratum's name was
        # being asked to second-guess a model that never spoke.
        #
        # Reviewed by hand before this changed: 50 of 50 were wire, feeds,
        # video or photo galleries, and every rejection was correct.
        #
        # The mechanism test rides INSIDE the Q as a lookup rather than
        # as an annotated column. An annotation has to be applied by
        # whoever builds the queryset, and a caller that forgets gets
        # `FieldError: Cannot resolve keyword` -- the review queue
        # returning a 500 rather than a wrong count. A predicate that
        # carries its own left-hand side cannot be used wrongly.
        return (
            Q(
                verification_confidence__gt=DECISIVE_MARGIN,
                storysniffer_result=False,
            )
            & Q(In(MECHANISM, MODEL_DECIDED))
            # Nothing overruled a link nothing ruled on. Every first
            # score written so far has `storysniffer_result` true and
            # would miss this anyway, which is exactly why it is stated:
            # the next one that does not would be labelled an override
            # of a verdict that was never reached.
            & not_a_first_score
        )
    return Q()


#: One cut per percentile, plus the top. Small enough to cache; the
#: alternative was 47,901 floats, which is the same answer at 400 kB.
PERCENTILE_STEPS = 101

#: Six hours. The margins do not change unless the crawler re-verifies,
#: and a scale that is half a day stale moves a reading by a point.
PERCENTILE_TTL = 60 * 60 * 6


def margin_cuts(start, end):
    """The cohort's margins at each percentile.

    The margin is a log-odds score running from -210 to 630,787 and it
    means nothing to a reader: it orders URLs and its scale is arbitrary.
    `predict_proba` is not the answer either -- it saturates, scoring
    97.5% of URLs at exactly 0.0 or 1.0, which is why this queue ranks on
    the margin in the first place.

    What can be said honestly is which percentile a URL falls in among
    the others the same classifier scored. These cuts turn a margin into
    that.
    """
    from explorer.models import UrlVerification

    since = f"{start:%Y%m%d}" if start else "any"
    until = f"{end:%Y%m%d}" if end else "any"
    key = f"discovery:margin-cuts:{since}:{until}"
    cuts = cache.get(key)
    if cuts is not None:
        return cuts

    values = sorted(
        v
        for v in in_cohort(UrlVerification.objects.all(), start, end).values_list(
            "verification_confidence", flat=True
        )
        if v is not None
    )
    if not values:
        return []
    last = len(values) - 1
    cuts = [
        values[min(last, (len(values) * step) // (PERCENTILE_STEPS - 1))]
        for step in range(PERCENTILE_STEPS)
    ]
    cache.set(key, cuts, PERCENTILE_TTL)
    return cuts


def what_storysniffer_said(accepted, margin):
    """Accepted or rejected, and how sure it was.

    `storysniffer_result` is the gate's actual answer -- the boolean
    `guess()` returns with its whitelist and blacklist applied -- which is
    what decided whether the URL went any further. The margin supplies the
    confidence only: its sign can disagree with the verdict, because a URL
    can score strongly and still be refused by a rule firing on the path.

    `DOUBTFUL_MARGIN` is the cut, already the band this queue draws its
    doubtful stratum on, so the wording and the sampling cannot drift.
    """
    if accepted is None:
        return None, None
    verdict = "Accepted" if accepted else "Rejected"
    if margin is None:
        return verdict, ""
    confidence = "low" if abs(margin) <= DOUBTFUL_MARGIN else "high"
    return verdict, f"{confidence} confidence"


def what_happened_after(accepted, status, fetched=None):
    """What became of a URL storysniffer accepted, and which outcome.

    Nothing happened to a URL it rejected -- rejection is what stopped it
    -- so there is no outcome to report and this says so rather than
    dressing the rejection up as a second verdict. That double reporting
    is what made the queue unreadable: a row whose model column said
    "not a story" also carried a pipeline column saying "Not a story",
    which invited the fair question of how a rejected URL had a pipeline
    result at all. It did not. It had its rejection, printed twice.

    Where it was accepted, this is the eventual result after extraction
    and any human review.
    """
    if accepted is False:
        return "not_processed", "\u2014"
    value = (status or "").strip().lower()
    if not value:
        return "unresolved", "Not processed"
    if value in KEPT_STATUSES:
        return "kept", "A story, kept as local news"
    if value in FILTERED_OUT_STATUSES:
        # Fetched and extracted to find this out, which is the opposite of
        # dropping it -- so the sentence leads with "A story".
        return "filtered_out", f"A story but {value.title()}, filtered out"
    if value in REJECTED_STATUSES:
        # Accepted by the gate and then found not to be an article. The
        # wording says when, because that is the difference between a URL
        # rule and a read of the body.
        if fetched is False:
            return "rejected", "Not an article, never fetched"
        return "rejected", "Not an article, after extraction"
    if value in UNRESOLVED_STATUSES:
        return "unresolved", "Not processed"
    # An unknown status shows itself rather than being forced into one of
    # the others. Guessing here is how `wire` came to mean "dropped".
    return "other", value


def storysniffer_was_wrong(accepted, status, fetched=None):
    """Did what happened next contradict the gate?

    Only one shape counts now. storysniffer accepted the URL and the
    pipeline then found it was not an article -- the gate let through
    something it should not have.

    The other direction is not observable here: a URL storysniffer
    rejected was never processed, so there is no outcome to contradict
    it. That is exactly why the second column is blank for those rows,
    and a reviewer reading one is being asked to judge the gate on the
    URL alone.
    """
    if not accepted:
        return False
    return what_happened_after(accepted, status, fetched)[0] == "rejected"


def percentile(margin, cuts):
    """The margin's percentile within the cohort, 0-100, or None.

    A percentile, and named one. It is not the probability that the URL
    is a story: `predict_proba` would be that and it saturates, scoring
    97.5% of URLs at exactly 0.0 or 1.0. Nothing here is calibrated, so
    printing "87% likely a story" would be the more comfortable lie.
    """
    if margin is None or not cuts:
        return None
    return max(0, min(100, bisect.bisect_right(cuts, margin) - 1))


#: What the discovery cohort opens on. The corpus this queue reviews was
#: discovered in March 2026 and the crawler has been idle since August,
#: so a relative window ("last 30 days") opens on nothing. The window
#: control still offers the usual ones; this is only where it starts.
DEFAULT_SINCE = "2026-03-01"
DEFAULT_UNTIL = "2026-04-01"


def window_for(params):
    """The cohort bounds a reviewer asked for, as aware datetimes.

    The same vocabulary the extraction queue uses -- `days` of 30, 90,
    365, all, or `custom` with two dates -- so the control reads the same
    on every queue. `all` is a real question here: 236,160 verifications
    exist and "has this publisher ever had a URL rejected" is asked of
    the lot.
    """
    from datetime import datetime, timedelta

    from django.utils import timezone

    def _date(value, fallback):
        try:
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return fallback

    window = params.get("days")
    if window == "all":
        return None, None
    if window == "custom" or not window:
        since = params.get("since") or (None if window else DEFAULT_SINCE)
        until = params.get("until") or (None if window else DEFAULT_UNTIL)
        return _date(since, None), _date(until, None)
    try:
        days = int(window)
    except ValueError:
        days = 30
    return timezone.now() - timedelta(days=days), None


def in_cohort(qs, start, end):
    """The links discovered in a window.

    Grouped on `candidate_links.discovered_at` and not on an article's
    publish date, because a row rejected before extraction has no
    article. Both bounds are applied to the one column so the window
    cannot be satisfied by two different rows.
    """
    if start is not None:
        qs = qs.filter(candidate_link__discovered_at__gte=start)
    if end is not None:
        qs = qs.filter(candidate_link__discovered_at__lt=end)
    return qs


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
#: Dicts, not pairs: `review/_verbs.html` renders `choice.value` and
#: `choice.label`, and a 2-tuple resolves to neither -- Django tries
#: attribute, then key, then numeric index, and "value" is none of them.
#: The list rendered with every option blank.
#:
#: One list per verb. The two questions have no answers in common, and a
#: single shared list offered "homepage" as a kind of story.

#: What kind of story, asked after "It is a story" -- and only the kinds
#: that change what the pipeline does with it.
#:
#: `News` is deliberately absent. Most stories are ordinary ones -- news,
#: sport, business, features -- and offering "News" as the shortest way
#: to say "it is a story" is how a sports story comes to be labelled
#: `news`: a category invented by the list rather than observed. "It is a
#: story" stands alone for all of them, and the pipeline classifies them
#: as it would any other.
#:
#: What remains are the three statuses no enrichment stage selects. Those
#: are worth a reviewer's click because naming one IS the instruction to
#: keep the article and stop before enrichment
#: (lnic_contracts.discovery_verdict.status_for). Anything that does not
#: change the outcome does not belong in a list somebody has to read.
#:
#: `wire` and `column` are here because both are filtered downstream --
#: a wire story is excluded from the corpus, a column is handled as
#: `not_article` by the extraction queue's own vocabulary. A reviewer
#: naming one is recording something the pipeline acts on.
#:
#: Neither overrides the pipeline's own finding. `status_for` maps only
#: the three statuses no enrichment stage selects; wire in particular is
#: settled by evidence in the body, which a reviewer judging a bare URL
#: has not seen, and extraction refuses a verdict over it. Offering the
#: label records what the person saw without promising it decides.
#: `non_english` is a story the pipeline cannot read: the classifier, the
#: CIN codebook and the enrichment prompts are written for English, so
#: fetching one spends a request and model budget to produce labels nobody
#: should trust. It keeps a link status of its own (`non_english`, via the
#: contract) rather than being folded into `wire` or `not_article`, because
#: how much of what these publishers write is not in English is a finding
#: about coverage -- and a reviewer who can see the language from the URL
#: has answered it more cheaply than a fetch would.
#: The kinds are the contract's, shared with the extraction queue so the
#: two cannot drift again. Only the shape differs: the console wants dicts.
STORY_KINDS = tuple(
    {"value": v, "label": label} for v, label in discovery_verdict.STORY_KINDS
)

#: What it is instead, asked after "Not a story".
#:
#: `Obituary Front` was here and is gone: an obituary front IS a section
#: front, and two names for one thing split the count that a fix would be
#: built from.
#:
#: `Section Front` and `Tag or author page` are kept apart from a story
#: deliberately: extraction has produced article rows for /profile/ pages
#: titled with the paper's own name, so "an article row exists" is not a
#: usable label, and a model trained on the two conflated learns the
#: wrong boundary.
#:
#: "Front" rather than "index": a section front and an obituary front are
#: what a newsroom calls them, and the stored values are unchanged so no
#: decision already recorded is affected.
#: What a discovery reviewer actually meets that is not a story. The list
#: started at the shapes a news site publishes and left out the ones a
#: CRAWLER produces -- a dead link, a feed URL, a search result, a PDF --
#: which are a large share of what reaches this queue and were all having
#: to go in as "Other". A count of "Other" says nothing a fix can be
#: built from, which is the whole reason the list exists.
#:
#: These write nothing on the crawler. "Not a story" leaves the status
#: alone -- it already excludes the row -- so the value is recorded on the
#: `ReviewDecision` for analysis and needs no contract change to add to.
NOT_STORY_KINDS = tuple(
    {"value": v, "label": label} for v, label in discovery_verdict.NOT_STORY_KINDS
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
    from lnic_contracts import discovery_verdict

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
        # Both halves, in one write. The status says "fetch this again";
        # the verdict says what the reviewer decided it is, and without
        # it the pipeline re-classifies the URL with the model that
        # misjudged it badly enough to put it in this queue. A reviewer
        # who said "opinion" watched the article get enriched.
        #
        # `obituary`, `opinion` and `weather` are statuses no enrichment
        # stage selects, so the type IS the instruction not to enrich --
        # there is no second flag to keep in step
        # (lnic_contracts.discovery_verdict.status_for).
        meta = dict(getattr(link, "meta", None) or {})
        # An empty kind is a complete answer: it says "an ordinary
        # story", and the pipeline classifies it as it would any other.
        # `or "other"` was here and was wrong -- it invented a category
        # for every story a reviewer did not categorise.
        meta[discovery_verdict.METADATA_KEY] = discovery_verdict.build(
            verdict=discovery_verdict.IS_A_STORY,
            kind=value or "",
            decided_by=getattr(user, "username", "") or "",
        )
        # `discovered` puts it back in the fetch queue, which is what
        # "it is a story" means -- except for a kind the pipeline should
        # never fetch at all. A reviewer who reads a URL and says "wire"
        # has already reached the conclusion a fetch, an extraction and a
        # wire check would reach; the kind used to be recorded here and
        # then ignored, so all three happened anyway.
        #
        # The mapping is the contract's, not this function's
        # (`link_status_for`): the same decision is read by the crawler,
        # and a copy here is a copy that can disagree.
        status = discovery_verdict.link_status_for(meta[discovery_verdict.METADATA_KEY])
        audited_update(
            user,
            [link],
            {"status": status, "meta": meta},
            action=(
                "discovery:restore"
                if status == discovery_verdict.VERIFIED_STATUS
                else "discovery:withhold"
            ),
            reason=(
                "Reviewer says this URL is a story"
                if status == discovery_verdict.VERIFIED_STATUS
                else f"Reviewer says this URL is {value}; not fetched"
            ),
        )
        after = status

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
                # Offered, not required. An ordinary story -- news,
                # sport, business, a feature -- is one click, and the
                # list is only for the three kinds that change what
                # happens next.
                value_required=False,
                value_blank="an ordinary story",
                values=STORY_KINDS,
            ),
            kernel.Verb(
                name=NOT_A_STORY,
                label="Not a story",
                sublabel="The status stands; the queue stops asking",
                past="confirmed",
                tone="reject",
                takes_value=True,
                values=NOT_STORY_KINDS,
            ),
        ),
        apply=apply_discovery,
    )
)
