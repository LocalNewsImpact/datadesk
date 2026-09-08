"""The extraction review queue's queries (SCOPE.md §2.3).

Automated triage flags articles it cannot use; a human decides what
happens to them. The March 2026 backfill's gap analysis defined three
cases, and each maps to a state the pipeline already records:

- **Paywall stubs** (981 in production) — `enrichment_skipped` whose skip
  reason says the stored text is a teaser or a login wall. Median length
  265 characters: too short for entity or geographic extraction, long
  enough for a CIN label and a byline. All of them carry a CIN and most a
  byline, so excluding them loses valid observations for CIN counts,
  byline rates and publication volume.
- **Minimal or empty captures** — `not_article`. A mix of genuine
  boilerplate and real articles whose text never came through. Only a
  human can tell them apart, and 38 of 206 carried more than 2000
  characters in March, which is why the length bands are a facet and not
  a note.
- **Scope mislabels** (69 in production) — scope-excluded articles kept
  for export with their scope recorded. Roughly 70% were locally bylined
  stories that merely referenced international subjects.

WHAT THE QUEUE MUST NOT HOLD
----------------------------
`removed_in_march_review` (3,695 rows) is not an extraction finding. Those
are deliberate membership removals a person already made from the March
sheet. Surfacing them would ask an operator to re-review 3,695 decisions
they have already taken, so they are excluded from every query here
whatever their status.

Read-only. Phase 2b adds the three dispositions as audited writes; this
module deliberately contains no write path.
"""

import contextlib
from contextlib import contextmanager

from django.db.models import Case as SQLCase
from django.db.models import Count, F, IntegerField, Q, When

from accounts.privileges import WRITE
from explorer.models import Article, Dataset
from explorer.scoping import narrow

PAYWALL_STUB = "paywall_stub"
MINIMAL_CAPTURE = "minimal_capture"
SCOPE_MISLABEL = "scope_mislabel"
#: A content type the detector called with little confidence and nothing
#: agreeing. Its own floor is 0.17 and 1,517 obituary verdicts were made
#: there, on a single body phrase -- enough to catch a feature about Jim
#: Morrison's grave and a charity for families of fallen first responders.
DOUBTED_CONTENT_TYPE = "doubted_content_type"
#: An article the pipeline stopped and parked because a FIELD was wrong --
#: a byline that is not a name, a body still in ciphertext. Held on
#: `in_review`, which is selected by no pipeline stage.
#:
#: This case has to exist or the hold is a black hole: `in_review` is not
#: one of the statuses the other cases select, so holding an article took
#: it out of the queue that is supposed to review it. Every field defect
#: the crawler holds would have been invisible here.
HELD_FOR_REVIEW = "held_for_review"
#: Exported, and never enriched, with nothing on the row saying why.
#:
#: `enrichment_skipped` is terminal AND exportable, so these articles look
#: finished: they reach BigQuery, count as local coverage, and carry no
#: scope, places or entities. Every selector requires `status = 'labeled'`,
#: so no run revisits them, and every other case here keys on a skip
#: reason -- which these do not have. Exported, counted, unenriched and
#: unreviewable.
#:
#: 23 were found in March Missouri on 2026-09-06, written by the backfill
#: spike that predates src/enrichment/orchestrator.py. Today's code always
#: records a reason, so this case is not for a bug that still writes them
#: -- it is for the next thing that does. A status no queue selects is a
#: black hole, which is the argument HELD_FOR_REVIEW already makes.
EXPORTED_UNENRICHED = "exported_unenriched"

#: Excluded as syndicated content.
#:
#: The largest exclusion in the corpus and, until now, the only large one
#: nobody could check: 9,335 articles in March 2026 Mizzou alone, against
#: 12,962 kept. Obituaries and minimal captures were reviewable and have
#: been reviewed in quantity; this was not, so a wrongly excluded article
#: was simply gone -- a Type II error with no other surface, which is the
#: expensive kind.
#:
#: The rows are ordered by the service they were attributed to rather than
#: doubt-ranked. There is no validated doubt signal for wire yet, and
#: inventing one would decide in advance what the review is supposed to
#: find out. Grouping by service is what makes precision per service fall
#: out of the review, and precision per service is what says which of them
#: can run unreviewed (99% precision and 95% recall) and which cannot.
#:
#: A local newsroom appearing as the attributed service is NOT evidence of
#: a mistake. The Missouri Independent and the Columbia Missourian are
#: newsrooms and syndicators both, so their content on another outlet is
#: correctly wire. What would be wrong is an outlet marked wire for
#: publishing its own work, and that question belongs to a reviewer
#: looking at the article, not to a heuristic guessing ahead of them.
WIRE_EXCLUSION = "wire_exclusion"

#: The topic rules, one case each.
#:
#: Separate rather than one "excluded by topic" case because a precision
#: figure attaches to a rule, not to a category: `matched_weather_signals`
#: and `matched_opinion_signals` are different rules and will not be
#: equally right. One chip each is what lets each be measured and promoted
#: on its own.
#:
#: Every row, not a doubted subset. `DOUBTED_CONTENT_TYPE` narrows
#: obituaries at `confidence_score < 0.30`, a threshold these never reach
#: -- the detector scores them in sixths and the weakest is 0.333, so that
#: filter would select none of them. Together they are 638 rows in March
#: 2026 Mizzou, small enough to read rather than sample.
WEATHER_EXCLUSION = "weather_exclusion"
OPINION_EXCLUSION = "opinion_exclusion"
PAYWALL_EXCLUSION = "paywall_exclusion"

#: Cases that are simply their status, with nothing further to narrow on.
PLAIN_STATUS_CASES = (
    WIRE_EXCLUSION,
    WEATHER_EXCLUSION,
    OPINION_EXCLUSION,
    PAYWALL_EXCLUSION,
    MINIMAL_CAPTURE,
    HELD_FOR_REVIEW,
)

# article_enrichment.skip_reason, as production actually holds it. Three
# spellings mean one finding: the bulk March update wrote
# paywall_stub_exported_unenriched, the LLM content gate writes
# paywall_stub, and the free rule ahead of it writes paywall_stub_rule
# (src/enrichment/orchestrator.py, PAYWALL_RULE_SKIP_REASON). All belong
# in the queue -- the rule diverts these before the model ever sees them,
# so if this list missed it they would leave the queue entirely, which is
# the opposite of what a new rule needs while it is being judged.
PAYWALL_STUB_SKIP_REASONS = (
    "paywall_stub",
    "paywall_stub_exported_unenriched",
    "paywall_stub_rule",
)

# Scope exclusions kept for export with the scope recorded. The bulk
# update wrote scope_recorded_not_excluded; the pipeline now writes
# scope_excluded_<category> (scope_excluded_international and so on), so
# that side is matched by prefix — none exist yet because the code
# shipped after the bulk update.
SCOPE_SKIP_REASONS = ("scope_recorded_not_excluded",)
SCOPE_SKIP_REASON_PREFIX = "scope_excluded_"


# --- which phase raised the flag, and how much to doubt it ------------------
#
# `not_article` is written in two places, and they mean different things.
#
# EXTRACTION (src/cli/commands/extraction.py) judges a body "furniture,
# not prose", sets the status and drops `text` while leaving `content`
# as captured. Such a row has no article_enrichment row and was never
# labelled.
#
# ENRICHMENT (src/enrichment/orchestrator.py, step 0) runs a content gate
# on articles that already reached `labeled`. Such a row always has an
# article_enrichment row, an enriched_at, and a CIN label.
#
# Discovery cannot produce either: its own `not_article_like` only
# escalates a capture to a browser fetch, and writes no article row.
#
# The discriminator is the enrichment row. Measured over the corpus it
# separates 1,051 extraction rows from 207 enrichment rows with no
# overlap on any of enrichment row, enriched_at, labelled, or text.
PHASE_EXTRACTION = "extraction"
PHASE_ENRICHMENT = "enrichment"

#: Surface a flag only when there is this much reason to doubt it. At
#: 175 extraction rejections per active day against ~815 articles, a
#: queue holding all of them is a backlog rather than a review.
DOUBT_THRESHOLD = 5

#: TownNews/BLOX serves paywalled bodies ROT47-encoded. `kE23=6 4=2DDlQ`
#: is `<table class="p`. Where the decode did not run the body reaches
#: extraction as ciphertext, reads as furniture, and is rejected -- so
#: this signature is never a correct rejection. All 11 in the corpus are
#: 102-108KB StatBot sports pages, 9 of them bylined.
ROT47_MARKERS = ("k^Am", "kE23=6", "lQA5C2?<Qm")


# --- what the pipeline recorded about its own verdicts ----------------------
#
# The content type detector writes confidence_score, reason and evidence to
# content_type_detection_telemetry for every row it decides -- 100% coverage,
# joined on article_id. That is the detector's own stated uncertainty, and it
# is a better signal than anything inferred here from the text afterwards.
#
# Coverage since telemetry began (2025-11-07): weather 100%, obituary 97.7%,
# opinion 95.6%, wire 12.8%. Wire is the exception because several paths can
# set it and only this one writes here; wire is judged on its detection
# method instead, below.

#: An obituary called on a phrase in the body with nothing else agreeing.
#: 1,098 rows, average confidence 0.17 and never above 0.25 -- the
#: detector's own floor. The evidence is usually a single phrase:
#: {"content": ["passed away"]}, 542 of them. That is enough to catch
#: "The grave of The Doors singer Jim Morrison has become a sh...",
#: "BackStoppers" (a charity for families of fallen first responders) and
#: two newspapers' own names. Where the URL path or the title agrees the
#: average rises to 0.38 and the calls are right.
#:
#: Weather and opinion have no content-only cases at all, so this shape
#: is specific to obituary.
CORROBORATING_EVIDENCE_KEYS = ("url", "title_patterns", "title")

#: Cross-domain canonical is 62% of all evidenced wire verdicts, and it is
#: the method behind the misattribution where a canonical pointing at a
#: same-site alias reads as syndication. Judged on the relationship rather
#: than the method: of 15,220 rows decided this way only 155 point at a
#: host sharing the publisher's own first label. Surfacing the method
#: itself would be 113 a day, nearly all of them correct.
WIRE_SUSPECT_METHOD = "canonical_cross_domain"


#: A headline that reads as a sentence rather than a name.
#:
#: An obituary headline is a person: "Alice Theresa Kline", "ANNA LEE
#: VANSKIKE", "Allen Ray Shaeffer (December 30, 1949 - March 9, 2026)".
#: A news story about a death is a sentence: "Carthage man killed in
#: motorcycle crash", "Community pays tribute to Missouri deputies",
#: "Former SEMO Sports Information Director Ron Hines passes away at 82".
#:
#: The discriminator is a lowercase word. Names are capitalised; verbs and
#: prepositions are not. `\m` and `\M` are Postgres word boundaries, and
#: they matter: written with `$` the allowlist only ever matched a
#: particle as the last word of the headline, so "Hendrik van der Berg"
#: read as a sentence and every Dutch surname went to review.
#:
#: Measured on the 236 March 2026 Mizzou obituaries a reviewer has ruled
#: on -- 208 upheld, 28 overturned:
#:
#:     held back as names   183 obituaries, 2 not
#:     sent to review        51 rows, 26 of them real errors (51.0%)
#:
#: Against a base rate of 11.9%, so a reviewer meets a mistake in every
#: second row rather than every eighth, and the March queue holds 66 rows
#: rather than 745.
#:
#: The two held back wrongly are "Rev. Jesse Jackson's Son Criticizes
#: Those Who Politicized Funeral", Title Case throughout and so carrying
#: no lowercase word, and "Taylor News". Not perfect; it reduces the false
#: positives, which is what it is for.
SENTENCE_HEADLINE = (
    r"\m(?!(of|the|a|an|and|or|to|in|at|on|for|van|von|der|den|de|del"
    r"|la|le|du|da|di|dos|jr|sr|nee)\M)[a-z]{2,}"
)


def _evidence_mentions(keys):
    """A Q matching telemetry whose evidence names any of these keys.

    Containment, not a JSON operator. `evidence` is TEXT in Postgres, so
    `has_any_keys` emitted `?|` and Postgres refused it -- which reached
    the page as "crawler database not connected", because the view catches
    DatabaseError and cannot tell a broken query from a broken connection.

    SQLite accepted the operator, so every test passed.
    """
    query = Q(pk__in=[])  # matches nothing, so an empty key list is empty
    for key in keys:
        query |= Q(detections__evidence__contains=f'"{key}"')
    return query


def evidence_is_corroborated(evidence) -> bool:
    """Did anything beyond a phrase in the body agree with the verdict?

    Accepts the JSON text the column actually holds as well as a decoded
    dict. `evidence` is TEXT in Postgres, not jsonb, and the two callers
    reach it through different layers.
    """
    if isinstance(evidence, str):
        import json

        try:
            evidence = json.loads(evidence)
        except ValueError:
            return False
    if not isinstance(evidence, dict):
        return False
    return any(key in evidence for key in CORROBORATING_EVIDENCE_KEYS)


def same_site_alias(publisher_host: str, canonical_host: str) -> bool:
    """Two hosts that are the same newsroom under different names.

    emissourian.com and missourian.com share no label and are not caught;
    a subdomain alias like nwaonline.com does. Compared on the first label
    rather than by substring, which matched kansascity.com against
    kansas.com -- two different newsrooms.
    """

    def head(host):
        host = (host or "").strip().lower().removeprefix("www.")
        return host.split(".")[0] if host else ""

    left, right = head(publisher_host), head(canonical_host)
    return bool(left) and left == right


def classification_doubt(
    status,
    confidence_score=None,
    evidence=None,
    wire_method=None,
    publisher_host=None,
    canonical_host=None,
) -> int:
    """How much reason there is to doubt a classification the pipeline made.

    Scored from what the pipeline recorded, not from the text. A verdict
    with no telemetry scores zero rather than a guess: absence of evidence
    is not evidence the call was wrong.
    """
    score = 0
    if status == "wire":
        if wire_method == WIRE_SUSPECT_METHOD and same_site_alias(
            publisher_host or "", canonical_host or ""
        ):
            score += 5
        return score

    if confidence_score is None:
        return 0
    # An obituary resting on a body phrase alone. Capped at 0.25 by the
    # detector itself, so this and the score below are one signal seen twice
    # -- the shape is the reason, the score is the symptom.
    if status == "obituary" and not evidence_is_corroborated(evidence):
        score += 4
    if confidence_score < 0.30:
        score += 2
    elif confidence_score < 0.50:
        score += 1
    return score


def prose_density(text):
    """Sentence enders per 1,000 characters.

    Length alone ranks the wrong rows first: the corpus band that reads
    least like prose averages 11,404 characters, because a 108KB table of
    box scores is long and a real 1,500-character story is not. Counting
    sentence enders inverts that -- writing runs 4-8 per 1,000, furniture
    and navigation under 1.
    """
    if not text:
        return 0.0
    per_thousand = len(text) / 1000.0
    if not per_thousand:
        return 0.0
    enders = text.count(". ") + text.count(".\n")
    return enders / per_thousand


def looks_rot47(text):
    """Undecoded TownNews premium body, rather than furniture."""
    return bool(text) and any(marker in text for marker in ROT47_MARKERS)


def doubt(article, enrichment=None):
    """How much reason there is to think this rejection is wrong.

    Two scales, because the phases leave different evidence. Both are
    tuned so DOUBT_THRESHOLD selects rows that look like real articles
    rather than a random slice.
    """
    text = article.text or ""
    content = article.content or ""
    bylined = bool((article.author or "").strip())

    if enrichment is not None:
        # The enrichment gate has two paths and only one records a
        # reason: `boilerplate_score >= HEURISTIC_REJECT` returns with no
        # explanation, and those 12 rows average 5,853 characters, 11 of
        # 12 bylined -- including an 18,044-character bylined feature. A
        # threshold with nothing to say for itself is the strongest
        # single signal that the rejection is wrong.
        score = 3 if not (enrichment.content_gate_reason or "").strip() else 0
        if len(text) >= 2000:
            score += 3
        elif len(text) >= 1000:
            score += 1
        if bylined:
            score += 2
        if (article.primary_label_confidence or 0) >= 0.70:
            score += 1
        return score

    # Extraction. `content` survives on 263 of 1,051 rows; the other 788
    # went down the paywall branch, which empties both fields and leaves
    # nothing on the row to judge -- only the raw HTML in GCS, for 30 days.
    score = 5 if looks_rot47(content) else 0
    density = prose_density(content)
    if density >= 4:
        score += 3
    elif density >= 2:
        score += 1
    if bylined:
        score += 2
    if content:
        score += 1
    return score


# Never in the queue, whatever the status: a human already decided.
HUMAN_REMOVAL_SKIP_REASON = "removed_in_march_review"

# The status each case selects on. Statuses are the pipeline's, never
# invented here (SCOPE.md §2.2).
CASE_STATUS = {
    PAYWALL_STUB: "enrichment_skipped",
    MINIMAL_CAPTURE: "not_article",
    SCOPE_MISLABEL: "enrichment_skipped",
    DOUBTED_CONTENT_TYPE: "obituary",
    HELD_FOR_REVIEW: "in_review",
    EXPORTED_UNENRICHED: "enrichment_skipped",
    WIRE_EXCLUSION: "wire",
    WEATHER_EXCLUSION: "weather",
    OPINION_EXCLUSION: "opinion",
    PAYWALL_EXCLUSION: "paywall",
}

CASE_LABELS = {
    PAYWALL_STUB: "Paywall stubs",
    MINIMAL_CAPTURE: "Minimal or empty captures",
    SCOPE_MISLABEL: "Wrong geographic scope",
    DOUBTED_CONTENT_TYPE: "Obituaries that read as stories",
    HELD_FOR_REVIEW: "Held: a field is wrong",
    EXPORTED_UNENRICHED: "Exported without enrichment",
    WIRE_EXCLUSION: "Excluded as wire",
    WEATHER_EXCLUSION: "Excluded as weather",
    OPINION_EXCLUSION: "Excluded as opinion",
    PAYWALL_EXCLUSION: "Excluded as paywalled",
}

CASE_NOTES = {
    WEATHER_EXCLUSION: (
        "Excluded as a forecast. The rule scores in sixths and half of "
        "these matched two signals of six, which is the end to start at."
    ),
    OPINION_EXCLUSION: (
        "Excluded as commentary. Same rule shape as weather, a different "
        "signal list, and its own precision to establish."
    ),
    PAYWALL_EXCLUSION: (
        "Excluded as behind a paywall, which is a fact about access "
        "rather than about the article."
    ),
    WIRE_EXCLUSION: (
        "Excluded as syndicated. The pipeline already tells local "
        "syndicators from wire services and files the first as `local`, "
        "so these are the rows it judged to be neither. The largest "
        "exclusion in the corpus and the last one without a review "
        "surface."
    ),
    PAYWALL_STUB: (
        "Text is a teaser or a login wall. The CIN label and byline are "
        "still usable, so exclusion loses valid observations."
    ),
    MINIMAL_CAPTURE: (
        "Genuine boilerplate and real articles whose text never arrived, "
        "mixed together. Check the long bands first."
    ),
    HELD_FOR_REVIEW: (
        "The pipeline stopped these rather than export them: a byline that "
        "is not a name, or a body still in ciphertext. They are out of the "
        "pipeline until somebody decides, and nothing but a decision "
        "releases them."
    ),
    DOUBTED_CONTENT_TYPE: (
        "Obituaries whose headline reads as a sentence rather than a "
        "name -- a story about a death rather than a death notice. "
        "Roughly half of these are wrongly excluded."
    ),
    SCOPE_MISLABEL: (
        "Excluded for being about somewhere else, and kept for export "
        "with the scope recorded. In March roughly 70% were locally "
        "bylined stories that merely referenced a foreign subject."
    ),
    EXPORTED_UNENRICHED: (
        "Marked not-enriched with no reason recorded, so nothing says "
        "what stopped them. They export and count as local coverage "
        "while carrying no scope, places or entities, and no stage will "
        "select them again."
    ),
}

# Captured-text length bands. The 2000+ band exists because the March
# analysis found 38 articles over 2000 characters among 206 flagged
# not_article — a full-length story is not a failed capture.
BANDS = (
    ("empty", "No text", 0, 0),
    ("stub", "1–499", 1, 499),
    ("short", "500–999", 500, 999),
    ("medium", "1000–1999", 1000, 1999),
    ("long", "2000 and over", 2000, None),
)
BAND_BOUNDS = {key: (low, high) for key, _label, low, high in BANDS}


#: Where a wire call wrote down its reason.
#:
#: `wire_detection` is the wire writers' own key; `detected_services` is
#: what the content-type detector records under
#: `content_type_detection.evidence` when it reaches the same verdict by
#: byline, dateline, metadata or URL. A row carrying either says which
#: method decided and can be judged on that method's record.
#:
#: Matched as text because these are Postgres `json`, not `jsonb`: the
#: column has no equality operator and Django renders `icontains` as
#: `metadata::text LIKE`, which is exact enough for two key names that
#: appear nowhere else.
WIRE_EVIDENCE_KEYS = ("wire_detection", "detected_services")


def _no_recorded_wire_evidence():
    """A wire call that never said why.

    Measured against the reviewed decisions on 2026-09-08: every method
    that recorded a reason was right every time it was checked --
    canonical_cross_domain 11 keeps and 0 restores, canonical+meta_author
    15 and 0, meta_author 5 and 0, the jsonld combinations 3 and 0. Both
    of the wrong calls a reviewer found sat in the rows that recorded
    nothing, 28 keeps to 2 restores.

    So the case holds what has no evidence behind it. That is 1,010 of
    March's 9,455 rows, and 47% of the corpus-wide 47,441 drops out
    without a reviewer ever needing to look at it.

    WHAT HAS ACTUALLY BEEN CHECKED, AND WHAT HAS NOT
    ------------------------------------------------
    Recording a method is not the same as having tested it, and the
    decisions alone cover the methods very unevenly:

        canonical_cross_domain      12,293 rows   800 read, 0 errors
        jsonld_author                3,104 rows    25 read, 0 errors
        meta_author                  2,276 rows     6 decisions
        og_distributor_category      1,470 rows    25 read, 0 errors
        everything else              3,255 rows    a handful or none

    The two 25-row samples were drawn on 2026-09-08 precisely because
    those methods had never had a single article reviewed while
    suppressing 4,574 rows between them. Zero errors in 25 puts the error
    rate under roughly 6% -- enough to rule out a weak method, not enough
    to tell 99% from 100%, and nobody should read it as more than that.

    So this is a ranking, not a claim that the rest are correct. When one
    of these methods is shown to be wrong it belongs back here, and the
    way to notice is that its rows still carry the method that decided
    them.
    """
    doubted = Q()
    for key in WIRE_EVIDENCE_KEYS:
        doubted &= ~Q(metadata__icontains=f'"{key}"')
    return doubted


def _case_q(case):
    """The rows one case selects, as a Q over Article.

    Exact values, not substrings: the vocabulary is closed and known, and
    a substring match on "stub" or "scope" would have swept in
    removed_in_march_review's neighbours as the pipeline grows.
    """
    if case == PAYWALL_STUB:
        return Q(status=CASE_STATUS[PAYWALL_STUB]) & Q(
            enrichment__skip_reason__in=PAYWALL_STUB_SKIP_REASONS
        )
    if case == SCOPE_MISLABEL:
        return (
            Q(status=CASE_STATUS[SCOPE_MISLABEL])
            & (
                Q(enrichment__skip_reason__in=SCOPE_SKIP_REASONS)
                | Q(enrichment__skip_reason__startswith=SCOPE_SKIP_REASON_PREFIX)
            )
            # `out_of_scope` is NOT selected here.
            #
            # This case used to carry `| Q(status="out_of_scope")`, added
            # when the status was believed unused and kept "until it is
            # confirmed unused". It is used now, and by exactly one
            # writer: the reviewer choosing "Out of scope" from the
            # disposition list (dispositions.TYPE_BECOMES). Selecting it
            # here re-flagged the rows a person had just disposed of --
            # 18 of them in production, all showing their own `reject`
            # and, because a decision existed, showing it with no buttons
            # at all, so they could not even be decided again.
            #
            # A status a human wrote is a decision, not a flag. That is
            # already the rule for the March removals
            # (HUMAN_REMOVAL_SKIP_REASON); this is the same rule.
        )
    if case == EXPORTED_UNENRICHED:
        # No reason recorded, in any of the three ways the row can say
        # nothing: no enrichment row at all, a null reason, an empty one.
        # Disjoint from PAYWALL_STUB and SCOPE_MISLABEL by construction --
        # they share this status and both require a reason.
        return Q(status=CASE_STATUS[EXPORTED_UNENRICHED]) & (
            Q(enrichment__isnull=True)
            | Q(enrichment__skip_reason__isnull=True)
            | Q(enrichment__skip_reason="")
        )
    if case == WIRE_EXCLUSION:
        return Q(status=CASE_STATUS[WIRE_EXCLUSION]) & _no_recorded_wire_evidence()
    if case in PLAIN_STATUS_CASES:
        # The status is the whole selector. DOUBTED_CONTENT_TYPE can narrow
        # obituaries because the detector records a confidence worth
        # narrowing on; these either record no confidence at all (the topic
        # detector's confidence counts matched signals rather than
        # estimating correctness) or record one that never falls below its
        # threshold.
        return Q(status=CASE_STATUS[case])
    if case == DOUBTED_CONTENT_TYPE:
        # Every obituary, not the low-confidence ones.
        #
        # This used to select `confidence_score < 0.30` and no
        # corroborating evidence, on the reading that a low score marks a
        # doubtful call. Measured against the reviews, it does not:
        #
        #   785 March 2026 Mizzou obituaries, scored 0.167 to 0.667
        #   311 fall under the old threshold
        #   every obituary a reviewer has decided scored 0.167
        #   of those, 208 upheld and 27 overturned -- 88.5% correct
        #
        # A score of one signal in six being right 88.5% of the time means
        # the number is a tally of matched signals, not a probability, and
        # it does not rank. The filter therefore did not surface doubtful
        # obituaries; it surfaced the bottom band and called it doubt.
        #
        # Worse for what comes next: it meant every label ever collected
        # came from that one band. A categoriser trained on them would
        # learn from a corner of the distribution and inherit its skew.
        # Drawing from the whole population was the prerequisite for the
        # model, and it is still what the labels have to come from -- but
        # not by reading 745 obituaries to find 28 mistakes.
        #
        # The headline separates them. An obituary headline is a name; a
        # story about a death is a sentence, and a sentence has a
        # lowercase word in it. See SENTENCE_HEADLINE for the measurement.
        return Q(status=CASE_STATUS[DOUBTED_CONTENT_TYPE]) & Q(
            title__regex=SENTENCE_HEADLINE
        )
    return Q()


def _flagged_q(cases=None):
    """Everything the queue holds, or only the named cases.

    The human-removal exclusion is applied here rather than per case, so
    no selector anywhere in this module can reach those 3,695 rows —
    including a status that has not been considered.
    """
    query = Q()
    for case in cases or CASE_STATUS:
        query |= _case_q(case)
    return query & ~Q(enrichment__skip_reason=HUMAN_REMOVAL_SKIP_REASON)


#: Characters of captured text. A generated, indexed column on
#: `articles` now (crawler #539); it used to be computed here as
#: `Length(Coalesce(content, text, text_excerpt))`, which read every body
#: out of TOAST on every query that asked. Same fallback order, so every
#: band and threshold means what it always meant.
TEXT_LENGTH = F("text_length")


def base_queryset_unscoped():
    """The flagged rows, before anyone's access narrows them.

    Split out for the scheduled worklist count, which asks what is IN a
    directory rather than what one person may see -- the work is the same
    whoever asks, and access decides which directories somebody is shown.

    Not for use in a request. Every page goes through `base_queryset`,
    which narrows.
    """
    return (
        Article.objects.select_related("candidate_link__source")
        .annotate(
            enr_skip_reason=F("enrichment__skip_reason"),
            enr_gate_reason=F("enrichment__content_gate_reason"),
            enr_is_news=F("enrichment__is_news_content"),
            enr_scope=F("enrichment__scope"),
            enr_present=F("enrichment__article_id"),
        )
        .filter(_flagged_q())
    )


def base_queryset(user):
    """Flagged articles, annotated with what the operator has to judge:
    how much text was captured, the reason the gate gave, the CIN label
    and whether a byline survived.

    Narrowed to the datasets `user` may write, which is also what the
    view's guard asked for -- the queue exists to be worked, so the rows
    on it are the ones this person could act on.
    """
    return narrow(
        Article.objects.select_related("candidate_link__source")
        .annotate(
            enr_skip_reason=F("enrichment__skip_reason"),
            enr_gate_reason=F("enrichment__content_gate_reason"),
            enr_is_news=F("enrichment__is_news_content"),
            enr_scope=F("enrichment__scope"),
            # Whether an enrichment row exists at all, which is what says
            # WHICH STAGE decided this article's status. Inferring it from
            # the nullable columns above fails on a row that exists with
            # all of them null.
            enr_present=F("enrichment__article_id"),
        )
        .filter(_flagged_q()),
        user,
        WRITE,
    )


#: How far back the queue looks unless somebody says otherwise.
#:
#: Every article ever crawled is not a queue, it is an archive. The corpus
#: is 164,000 articles and the flagged ones go back to 2025; a reviewer
#: opening this is working on what the pipeline is doing now, and a page
#: that starts by showing December's mistakes buries September's.
#:
#: A default that hides rows has to be visible, or it reads as data
#: missing. The window is a filter chip like any other and says which one
#: it is.
#: The window is what keeps the counting cheap: the paginator counts the
#: whole set and `case_facets` runs a conditional aggregate per case over
#: it, so this bounds the work the page does before it can draw anything.
DEFAULT_DAYS = 30

#: What the window can be set to. `all` is here because a question about
#: a publisher's history is a real question, just not the default one.
DAY_WINDOWS = (
    ("30", "Last 30 days"),
    ("90", "Last 90 days"),
    ("365", "Last year"),
    ("all", "Everything"),
    ("custom", "Custom…"),
)

#: The value that means "read the two dates instead of counting back".
#:
#: The windows answer "recently"; a question about a particular month --
#: the March corpus, the week a publisher changed its template -- has no
#: number of days that expresses it, and picking 90 and reading past the
#: rows you did not want is not the same thing.
CUSTOM = "custom"


def _parse_date(value):
    """A `YYYY-MM-DD` from the form, or None.

    None for anything else, including an empty string and the browser's
    own idea of a partial date. A half-typed date reads as "no bound"
    rather than as an error: the field is a filter, and refusing the page
    while somebody is still typing in it would be worse than showing them
    a wider range.
    """
    from datetime import date

    try:
        return date.fromisoformat((value or "").strip())
    except (ValueError, TypeError):
        return None


#: What a row is flagged AS, and what that means in words.
#:
#: The column headed "Flagged as" showed the article's STATUS -- which
#: for a paywalled stub reads "Exported unenriched". That is where the
#: article ended up, not what anybody flagged: the flag is
#: `paywall_stub`, and what a reviewer needs to know is that the story is
#: cut off by a login prompt. A status in this column asks the reviewer
#: to work backwards from an outcome to a reason, on every row.
#:
#: Keyed on what the pipeline actually recorded -- the enrichment skip
#: reason, the content gate's reason, the review note's claim -- because
#: those are the flags. The status stays on the row, in its own place.
FLAGS = {
    "paywall_stub": ("paywall_stub", "Story cut off by login prompt"),
    "paywall_stub_exported_unenriched": (
        "paywall_stub",
        "Story cut off by login prompt",
    ),
    # Said differently on purpose. The finding is the same, the evidence
    # is not: a phrase and a length, which a reviewer can check and this
    # project can retune, rather than a model's judgement. A reviewer who
    # cannot tell the two apart cannot tell whether the rule is right.
    "paywall_stub_rule": (
        "paywall_stub_rule",
        "Login prompt matched by rule, before AI review",
    ),
    "scope_recorded_not_excluded": (
        "scope_recorded",
        "Recorded as about somewhere else, and kept",
    ),
    "byline_not_a_name": ("byline_not_a_name", "Byline is not a person's name"),
    "text_is_ciphertext": ("text_is_ciphertext", "Body never decoded"),
    "removed_in_march_review": ("removed_by_hand", "Taken out in the March review"),
}

#: A scope exclusion carries the place in the reason itself
#: (`scope_excluded_ukraine`), so it is matched by prefix and the place is
#: kept: "about somewhere else" is the finding, and which somewhere is
#: what a reviewer judges.
SCOPE_EXCLUDED_FLAG = "scope_excluded"


def _words(flag, hint):
    """A flag's words, revised where somebody has revised them."""
    from review.vocabulary import flag_words

    label, revised = flag_words(flag, declared_label=flag, declared_hint=hint)
    return (label, revised)


def flag_of(article):
    """(flag, hint) for a row: what it was flagged as, and what that means.

    Falls back to the shape the queue matched on rather than to the
    status. A row with no recorded reason is in this queue for something
    the query found -- a body too short to be a story, a content type the
    detector barely believed -- and saying so is more use than repeating
    where the article ended up.
    """
    reason = getattr(article, "enr_skip_reason", "") or ""
    if reason in FLAGS:
        return _words(*FLAGS[reason])
    if reason.startswith(SCOPE_SKIP_REASON_PREFIX):
        place = reason[len(SCOPE_SKIP_REASON_PREFIX) :].replace("_", " ")
        return _words(
            SCOPE_EXCLUDED_FLAG, f"Excluded as about {place or 'somewhere else'}"
        )

    gate = getattr(article, "enr_gate_reason", "") or ""
    if gate:
        return _words(gate, "Enrichment's content gate stopped it")

    # Imported here rather than at module scope: dispositions imports this
    # module for its vocabulary, and a top-level import would close the
    # circle.
    from review.dispositions import claim_under_review

    claim = claim_under_review(article)
    if claim:
        return _words(*FLAGS.get(claim, (claim, "Held for review")))

    # No recorded reason. The case the row matched is the flag, and the
    # cases are keyed on status -- so this reads the status to name the
    # FLAG, which is not the same as showing the status in its place.
    status = getattr(article, "status", "")
    if status == CASE_STATUS[MINIMAL_CAPTURE]:
        return _words("minimal_capture", "Body is too short to be a story")
    if status == CASE_STATUS[DOUBTED_CONTENT_TYPE]:
        return _words("doubted_type", "Called an obituary; the headline is a sentence")
    if status == CASE_STATUS[EXPORTED_UNENRICHED]:
        # Reached only with no reason of any kind: every branch above
        # returns when one exists, and this status carries one whenever
        # the pipeline wrote it.
        return _words(
            "exported_unenriched", "Exported, never enriched, no reason given"
        )
    return _words("flagged", "")


def _dated_on_or_after(moment):
    """publish_date on or after `moment`, or -- where there is none -- created_at.

    The same fallback COALESCE(publish_date, created_at) expressed, but
    as two indexed comparisons rather than one function the planner
    cannot see through.

    With COALESCE, no index on either date can be used, so the planner
    has nowhere to start but the dataset side: it walked all 236,815
    Mizzou candidate links, looked up each one's article, and only THEN
    applied the date. 27 seconds for the facet chips, 7 for the count,
    every page load. Written as `publish_date >= x OR (publish_date IS
    NULL AND created_at >= x)`, both branches hit
    ix_articles_publish_date_created, the planner starts from the 11k
    articles in the window, and the same count takes one second.
    """
    return Q(publish_date__gte=moment) | (
        Q(publish_date__isnull=True) & Q(created_at__gte=moment)
    )


def _dated_before(moment):
    """The upper bound, same shape. Exclusive: `until` is a date, and a
    row published at 14:00 on that date is on it."""
    return Q(publish_date__lt=moment) | (
        Q(publish_date__isnull=True) & Q(created_at__lt=moment)
    )


def _dated_within(start, end):
    """Both bounds, grouped so the index sees both.

    `_dated_on_or_after(a) & _dated_before(b)` says the same thing as
    `(A or B) and (C or D)`, and the planner can only give a bitmap scan
    one of the two OR-groups: it ranged on `publish_date >= a` -- every
    row since March, 101,167 of them -- and applied `< b` on the heap.
    Regrouped as `(A and C) or (B and D)`, each arm is one range over
    exactly the rows in the window.
    """
    return (Q(publish_date__gte=start) & Q(publish_date__lt=end)) | (
        Q(publish_date__isnull=True) & Q(created_at__gte=start) & Q(created_at__lt=end)
    )


def _within_the_window(qs, params):
    """Narrow to the chosen window, on the date a reader would recognise.

    `publish_date` is what the row shows and what a reviewer means by
    "recent". It is not always there -- an extraction that failed to find
    one leaves it null, and those are exactly the rows this queue is for
    -- so the crawl date stands in where it is missing. Filtering on
    publish_date alone would hide the worst captures.
    """
    from datetime import timedelta

    from django.utils import timezone

    window = params.get("days") or str(DEFAULT_DAYS)
    if window == "all":
        return qs
    if window == CUSTOM:
        return _between_two_dates(qs, params)
    try:
        days = int(window)
    except ValueError:
        days = DEFAULT_DAYS
    cutoff = timezone.now() - timedelta(days=days)
    # A row with neither date is kept. Those are extractions that found
    # no publish date and were written before created_at was populated --
    # the worst captures in the corpus, and the ones this queue is for.
    # Dropping them would make the window hide exactly what it should
    # surface.
    return qs.filter(
        _dated_on_or_after(cutoff)
        | (Q(publish_date__isnull=True) & Q(created_at__isnull=True))
    )


def _between_two_dates(qs, params):
    """Narrow to a chosen range, on the same date the windows use.

    Either bound may be missing, and one alone is a real question -- "since
    the template changed", "everything before the March export". With
    neither, this is the whole corpus, which is what `Everything` already
    means and is the honest reading of a custom range nobody filled in.

    Rows with no date at all are kept for the windows, because they are the
    worst captures and the queue exists for them. They are dropped here: a
    reviewer asking for one month is asking for rows they can place in it,
    and an undated row is not in any month.
    """

    since = _parse_date(params.get("since"))
    until = _parse_date(params.get("until"))
    if not since and not until:
        return qs
    from datetime import datetime, time, timedelta

    from django.utils import timezone as tz

    # Date bounds become timestamp bounds. `since` at midnight, and
    # `until` as midnight of the FOLLOWING day with a strict less-than,
    # which is "on or before `until`" without a ::date cast -- the cast is
    # what stopped the index being used.
    start = since and tz.make_aware(datetime.combine(since, time.min))
    end = until and tz.make_aware(datetime.combine(until + timedelta(days=1), time.min))
    if start and end:
        return qs.filter(_dated_within(start, end))
    if start:
        return qs.filter(_dated_on_or_after(start))
    return qs.filter(_dated_before(end))


def _in_dataset(qs, slug):
    """Articles in one dataset, by the column on the article itself.

    This has been three things. Through dataset_sources: slug -> dataset
    -> its 211 sources -> every candidate link on them -> each link's
    article -> THEN the date; 27 seconds for the facet chips. Then the
    link's `dataset_id`, which took the paginator 7.4s -> 0.11s but still
    joined candidate_links -- and on an instance with 128 MB of
    shared_buffers, the hash of that join read 24,331 pages on every
    count. `articles.dataset_id` is filled from the link
    (MizzouNewsCrawler#540), so the count is a bitmap over the dataset's
    own rows.

    One definition because the page and its filter dropdowns both ask
    this question, and a dropdown built over a different population than
    the list offers values that return nothing.
    """
    return qs.filter(dataset_id__in=Dataset.objects.filter(slug=slug).values("id"))


def _apply_common(qs, params):
    """Filters shared by the queue and its facet counts."""
    qs = _within_the_window(qs, params)
    # An unrecognized case reads as no case filter rather than an error.
    if (case := params.get("case")) and case in CASE_STATUS:
        qs = qs.filter(_case_q(case))
    if slug := params.get("dataset"):
        qs = _in_dataset(qs, slug)
    if publisher := params.get("publisher"):
        # Publishers are searched by name: a hostname is not an
        # identifier and must not be matched on (it changes, and
        # the same one can front two records).
        qs = qs.filter(candidate_link__source__canonical_name__icontains=publisher)
    if method := params.get("method"):
        # WHICH RULE DECIDED, from `metadata.wire_detection`, where each
        # rule records the signals it fired on under `detected_by`.
        #
        # This is the axis precision is measured along, and the axis the
        # methods differ along: URL patterns and the MediaCloud lookup run
        # at 99%, while the local-syndication rules -- byline
        # identification, copyright-text matching -- are the tricky ones.
        # Reviewing "wire" as one undifferentiated pile compares none of
        # them against each other.
        #
        # Matched as text: `metadata` is Postgres `json`, not `jsonb`, so
        # a key lookup would emit an operator Postgres refuses on this
        # column. Django renders icontains as `metadata::text LIKE`, which
        # is exact enough here because these are distinctive rule names.
        qs = qs.filter(metadata__icontains=method)
    if service := params.get("service"):
        # The syndication the wire writers attributed the article to,
        # recorded in `articles.wire`. Matched as text because the column
        # holds a JSON array and one article can name several -- "NPR"
        # and "NPR, The Associated Press" are both NPR's to answer for.
        #
        # This is the axis the wire case is worked along. Several of the
        # methods behind these attributions are already at or above 99%
        # precision, and those do not earn a reviewer's time; the filter
        # is what lets the uncertain ones be worked without the certain
        # ones burying them.
        qs = qs.filter(wire__icontains=service)
    if skip := params.get("skip"):
        qs = qs.filter(enrichment__skip_reason=skip)
    if label := params.get("label"):
        qs = qs.filter(primary_label=label)
    if (byline := params.get("byline")) == "yes":
        qs = qs.exclude(Q(author__isnull=True) | Q(author=""))
    elif byline == "no":
        qs = qs.filter(Q(author__isnull=True) | Q(author=""))
    return qs


def _apply_band(qs, band):
    low, high = BAND_BOUNDS[band]
    qs = qs.filter(text_length__gte=low)
    if high is not None:
        qs = qs.filter(text_length__lte=high)
    return qs


def flagged_total():
    """How many articles are flagged, across the whole corpus.

    Deliberately unscoped, and deliberately not `queued(...).count()`.
    The landing page's figure is a corpus statistic cached once for
    everybody, sitting beside the corpus article count — scoping it would
    mean a cache entry per person for a number nobody acts on directly.
    The link to the queue *is* scoped: it only renders for people who may
    work it, and the queue itself shows them their own datasets.
    """
    return Article.objects.filter(_flagged_q()).count()


def doubtful_q():
    """Rows there is recorded reason to doubt, as a Q.

    Expressed in SQL rather than scored in Python so the queue can still
    be paginated and counted in the database.

    Paywall stubs and scope mislabels are carried whole: they are the
    cases the queue was built for, they are already small, and nothing
    here narrows them.

    The two that need narrowing are narrowed:

    - `not_article` at 175 a day is a backlog. Kept where the enrichment
      gate gave no reason for itself (12 rows averaging 5,853 characters,
      11 of 12 bylined, one an 18,044-character bylined feature), where
      the capture is long, where a byline survived, or where the body is
      undecoded ROT47 -- never a correct rejection.
    - an obituary whose headline reads as a sentence rather than a name.
      This replaced "called below 0.30 with neither URL nor title
      agreeing", which did not narrow on anything: every obituary a
      reviewer has ruled on scored 0.167, the floor, and 88.5% of them
      were right. See SENTENCE_HEADLINE.
    """
    reasonless_gate = Q(enrichment__isnull=False) & Q(
        enrichment__content_gate_reason__isnull=True
    )
    doubted_not_article = _case_q(MINIMAL_CAPTURE) & (
        reasonless_gate
        | Q(text_length__gte=2000)
        | ~Q(author__isnull=True) & ~Q(author="")
        | Q(content__contains="k^Am")
    )
    return (
        _case_q(PAYWALL_STUB)
        | _case_q(SCOPE_MISLABEL)
        | doubted_not_article
        | _case_q(DOUBTED_CONTENT_TYPE)
        # Never narrowed. A held article is stopped and waiting on a person;
        # leaving it off the landing view is what holding it would mean if
        # nobody were told.
        | _case_q(HELD_FOR_REVIEW)
        # Same argument: these are already invisible everywhere else, so
        # a landing view that hid them would be the defect again.
        | _case_q(EXPORTED_UNENRICHED)
    )


#: Cases deliberately absent from the landing view.
#:
#: The landing view holds what there is RECORDED REASON to doubt. For
#: these there is none: the wire writers record which service they
#: matched, not how sure they were, and the topic detector's confidence
#: counts matched signals rather than estimating correctness. Nothing
#: distinguishes a wire row worth reading from one that is not, so putting
#: all 47,419 on the landing view would bury the cases that do carry a
#: reason -- which is the backlog problem the narrowing exists to solve.
#:
#: They are not hidden. Each has a chip with its own count, and choosing
#: it shows every row. That is the difference between a case nobody is
#: shown and a case nobody is shown BY DEFAULT.
#:
#: Give one a doubt signal and it belongs in `doubtful_q`. The obituary
#: case earned its way in that way, on the headline.
CASES_OFF_THE_LANDING_VIEW = frozenset(
    {
        "wire_exclusion",
        "weather_exclusion",
        "opinion_exclusion",
        "paywall_exclusion",
    }
)

#: Filters that say WHICH CORPUS to work, not which rows to see.
#:
#: A scope keeps the landing narrowing on: what is left inside it is still
#: a backlog nobody works, which is the whole reason the narrowing exists.
#: The queue holds what there is reason to doubt, and that is all it is
#: for.
_SCOPES = ("dataset", "days", "since", "until", "state")

#: Filters that ask for a particular set of rows, and so lift the
#: narrowing: asking for a case is asking to see what matches.
#:
#: `dataset` is NOT one of them. Choosing a dataset says which corpus to
#: work, not that the reader wants everything flagged in it -- it is a
#: scope, and what is left inside it is still a backlog. Asking for a
#: case, a band or a publisher is asking to see a particular set.
#:
#: It was on this list, and that was survivable while the flagged set was
#: 1,956 rows for Mizzou. Once wire, weather, opinion and paywall had
#: cases it was 60,169, and picking a dataset turned the narrowing off and
#: asked the page to count all of them nine times over for the facet
#: chips. The page stopped answering.
_EXPLICIT = (
    "case",
    "band",
    "skip",
    "service",
    "method",
    "label",
    "byline",
    "publisher",
    "all",
)


def _asked_for_something(params) -> bool:
    return any(params.get(key) for key in _EXPLICIT)


def _population(qs, params, *, landing_narrowing=True):
    """Everything the queue is asking about, before any one facet narrows it.

    The rows and the facet counts have to come from the same set, and did
    not. `queued` dropped answered questions and, on the landing view,
    narrowed to what there is recorded reason to doubt; the band and case
    counts skipped both and were computed straight off `_apply_common`.
    So the chips counted rows the list would not show -- decided ones
    always, and on a bare queue the whole flagged backlog. Filtering to
    one month of one dataset still showed bands adding to 1,218 against a
    shorter list, which reads as the filters not being applied at all.

    `params` is what the reader asked for, even where the caller has
    dropped a facet from the queryset to count it: `case_facets` removes
    `case` so each chip shows its own total, and that must not turn the
    landing-view narrowing back on for a reader who did choose a case.

    `landing_narrowing` is off for the facet counts, because a chip
    promises what clicking it will show and clicking it IS an explicit
    filter -- which switches that narrowing off. Counting the chips the
    same way as the landing list made "No text" read 0, and that band
    exists precisely to surface the captures the narrowing hides.
    """
    # A question somebody already answered is not a question. `accept`
    # writes nothing to the article -- its status already excludes it --
    # so without this an accepted article matches its case forever and is
    # asked about on every visit.
    #
    # Keyed on (article, question), never on the article alone: a byline
    # later found to be garbage is a NEW question about an article whose
    # classification was settled, and must still be askable.
    if params.get("state") != "all":
        qs = _without_answered(qs)

    # The landing view holds what there is recorded reason to doubt: at
    # 175 extraction rejections per active day against roughly 815
    # articles, the unfiltered queue is a backlog nobody works.
    #
    # Any explicit filter turns it off. Asking for the empty band, or a
    # case, or one publisher, is asking to see what matches -- and the
    # empty band exists precisely to show the captures this narrowing
    # would otherwise hide.
    if landing_narrowing and not _asked_for_something(params):
        # Matched by id rather than by filtering the rows and calling
        # `.distinct()`. The telemetry join can repeat a row, and DISTINCT
        # over a selected row fails in Postgres -- "could not identify an
        # equality operator for type json" -- because `articles` carries
        # json columns. `IN` de-duplicates without comparing them.
        doubtful_ids = qs.filter(doubtful_q()).values("id")
        qs = qs.filter(id__in=doubtful_ids)
    return qs


@contextmanager
def hash_joins_for_the_queue():
    """Keep the planner off nested loops for the queue's reads.

    The population is filtered on both sides of a join -- dataset on
    candidate_links, status and date on articles -- and the planner has
    to pick a side to start from. It picks the dataset: 236,908 Mizzou
    links, one random index probe into articles for each, and then the
    date. Fresh statistics did not change its mind.

    That plan's cost is whatever is in memory. Measured back to back on
    the same query with nothing else running: 6s, 22s, 62s. The hash
    plan reads both sides once and was 4s every time.

    SET LOCAL, so it lasts exactly one transaction and touches nothing
    else the connection does. This is a pin, not a fix: the fix is
    `dataset_id` on `articles`, which removes the join from the filter
    and makes the population a single index scan.
    """
    from django.db import connections, transaction

    with transaction.atomic(using="crawler"):
        with connections["crawler"].cursor() as cursor:
            cursor.execute("SET LOCAL enable_nestloop = off")
        yield


def queued(params, user):
    """The queue itself: longest captures first.

    Length descending is the useful default — the wrongly flagged
    articles are the long ones, and putting them on the first page is the
    point of the queue.
    """
    qs = _population(_apply_common(base_queryset(user), params), params)
    band = params.get("band")
    if band in BAND_BOUNDS:
        qs = _apply_band(qs, band)
    # `text_length` is a column with a descending index, so "longest
    # first" is an index scan. It used to be computed per row, which read
    # every body in the population to rank the first page: 16.8s, now 1s.
    return qs.order_by("-text_length", "-created_at")


def _without_answered(qs):
    """Drop rows a decision has already settled.

    `accept` writes nothing to the article -- its status already excludes
    it from processing -- so an accepted article goes on matching its case
    and is asked about on every visit. This is what stops that.

    A decision settles one claim about one article, not the article. The
    pair is (article, the status it was reviewed under), so an article
    whose status later changes raises a new question and comes back. That
    is the intent: a byline found to be garbage months after the
    classification was settled has to be askable.

    Two queries rather than a subquery: the decisions are in the
    application database and the articles are in the crawler's, and
    Postgres does not join across databases. The decisions are the small
    side, and only those on statuses the queue can select are read.
    """
    from collections import defaultdict

    from review.models import ReviewDecision

    settled = defaultdict(list)
    rows = ReviewDecision.objects.filter(subject_type="article").values_list(
        "subject_id", "claim", "after"
    )
    for article_id, claim, after in rows:
        # The claim the decision answered.
        if claim:
            settled[claim].append(article_id)
        # AND the status the decision itself wrote.
        #
        # Six of the eight dispositions write a status this queue also
        # selects on -- "Not an article" writes `not_article`, which is
        # the minimal-capture case; "Obituary" writes `obituary`, which
        # is the doubted-type case; "Paywalled stub" writes
        # `enrichment_skipped`, which is two cases -- so a disposed
        # article came straight back on the next page load, now with its
        # own `reject` visible on the row. The reviewer was being asked
        # to re-decide what they had just decided, and the queue never
        # emptied.
        #
        # A later status change still raises a new question, which is the
        # point of keying on the pair: it will match neither the claim
        # answered nor the status this decision wrote.
        #
        # Neither is filtered against "statuses a case selects on". It
        # was, and `out_of_scope` escaped: it is not a value in
        # CASE_STATUS but the scope case selected it anyway, through an
        # extra clause of its own. So the one disposition whose status
        # the queue could still see was the one the gate let through, and
        # 18 rows in production came back permanently -- decided, and
        # therefore rendered with no buttons to decide them again.
        #
        # A status nothing selects on costs a key in a dict that no row
        # will ever match. A status something selects on, left out, is
        # this bug. There is no version of this worth gating.
        if after and after != claim:
            settled[after].append(article_id)
    if not settled:
        return qs

    answered = Q()
    for claim, ids in settled.items():
        answered |= Q(status=claim, id__in=ids)
    return qs.exclude(answered)


def band_facets(params, user):
    """Counts per length band, ignoring any band already selected.

    A facet that counted only the selected band would always read as the
    result count and tell the operator nothing.

    The band itself is the one thing not applied; everything else the
    list does, including dropping answered questions, is applied here
    through `_population` so a chip cannot promise rows the list will
    not show.
    """
    qs = _population(
        _apply_common(base_queryset(user), params), params, landing_narrowing=False
    )
    counts = qs.aggregate(
        **{
            key: Count(
                SQLCase(
                    When(_band_when(low, high), then=1), output_field=IntegerField()
                )
            )
            for key, _label, low, high in BANDS
        }
    )
    selected = params.get("band")
    return [
        {
            "key": key,
            "label": label,
            "count": counts[key],
            "selected": selected == key,
        }
        for key, label, _low, _high in BANDS
    ]


def _band_when(low, high):
    query = Q(text_length__gte=low)
    if high is not None:
        query &= Q(text_length__lte=high)
    return query


def case_facets(params, user):
    """Counts per case, ignoring any case already selected."""
    # .copy() rather than dict(): a QueryDict's dict() flattens to lists.
    scoped = params.copy()
    scoped.pop("case", None)
    # `scoped` builds the queryset -- without the case, so each chip counts
    # its own -- while `params` says what the reader actually asked for.
    qs = _population(
        _apply_common(base_queryset(user), scoped), params, landing_narrowing=False
    )
    band = params.get("band")
    if band in BAND_BOUNDS:
        qs = _apply_band(qs, band)
    counts = qs.aggregate(
        **{
            case: Count(
                SQLCase(When(_case_q(case), then=1), output_field=IntegerField())
            )
            for case in CASE_STATUS
        }
    )
    selected = params.get("case")
    return [
        {
            "key": case,
            "label": CASE_LABELS[case],
            "note": CASE_NOTES[case],
            "count": counts[case],
            "selected": selected == case,
        }
        for case in CASE_STATUS
    ]


#: The signals the wire rules record under `detected_by`, measured across
#: March 2026 Mizzou on 2026-09-07 (7,555 rows carrying `wire_detection`):
#:
#:     canonical_cross_domain      4,807
#:     meta_author                 2,514
#:     jsonld_author               1,159
#:     og_distributor_category       381
#:     jsonld_isBasedOn               95
#:     jsonld_mainEntity              95
#:     jsonld_contentSourceCode       95
#:
#: Listed rather than read from the rows because reading them means
#: decoding every metadata blob in the queue, and the set changes when a
#: rule is added, not when the corpus does.
WIRE_METHODS = (
    "canonical_cross_domain",
    "meta_author",
    "jsonld_author",
    "og_distributor_category",
    "jsonld_isBasedOn",
    "jsonld_mainEntity",
    "jsonld_contentSourceCode",
)


#: The scopes a filter dropdown is built under.
#:
#: A dropdown exists to be chosen from, so every value on it should
#: return rows on the page the reviewer is looking at. `state` is a
#: scope for the list but not for the vocabularies: it says which
#: decisions to show, and a service should not vanish from the filter
#: because the last article naming it was decided.
_VOCAB_SCOPES = ("dataset", "days", "since", "until")

#: How long the filter dropdowns are held.
#:
#: They are read from the corpus, and the corpus does not change between
#: two page loads by the same reviewer. Five minutes, matching
#: review/todo.py: long enough that paging through a queue does not
#: rebuild them, short enough that what a crawl added shows up while
#: somebody is still working.
VOCAB_CACHE_SECONDS = 300


def _scoped_to_the_page(qs, params):
    """The dataset and window the page is under, and nothing else.

    The vocabularies go through this rather than reading the whole
    corpus. Unscoped they cost what the page used to: a DISTINCT over
    every article and a pass over every flagged wire row, on every load,
    regardless of which dataset or month was being worked. They were the
    slowest thing left once the counts were fixed -- 5.2 seconds of a
    cold page, and never the rows on it.
    """
    qs = _within_the_window(qs, params)
    if slug := params.get("dataset"):
        qs = _in_dataset(qs, slug)
    return qs


def service_names(value):
    """The syndication names in one `articles.wire` value.

    Three shapes, all of them in production on 2026-09-08: a JSON array
    of names (18,117 flagged rows), an object carrying `provider`
    (24,521), and a bare string. Only the arrays used to be read, so
    nine services covering 394 articles named a syndication that could
    not be chosen in the filter built to work them -- an exclusion with
    no way to review it, which is the expensive kind.

    A shape nobody anticipated names nothing rather than raising. This
    fills a dropdown; an unreadable value should cost one absent option,
    not the page.
    """
    if not value:
        return ()
    if isinstance(value, str):
        value = [value]
    elif isinstance(value, dict):
        value = [value.get("provider")]
    if not isinstance(value, list):
        return ()
    return tuple(
        name.strip() for name in value if isinstance(name, str) and name.strip()
    )


def _wire_services(user, params=None, limit=60):
    """Every syndication named on a flagged wire row, by volume.

    Read from the rows rather than from a list, so the filter offers what
    is actually there. Ordered by how many articles each accounts for,
    because that is the order in which reviewing them is worth anything:
    the head of this list is where the corpus was actually spent.

    Grouped in the database, weighted here. The wire case holds 47,423
    articles and 446 distinct `wire` values, and this used to fetch one
    row per article and count them in Python -- 47,423 JSON values across
    the wire on every page load, for a dropdown. Counting the distinct
    values and multiplying by how many rows hold each gives the same
    tally from 446 rows.
    """
    import json
    from collections import Counter

    from django.db.models import Count, TextField
    from django.db.models.functions import Cast

    rows = (
        _scoped_to_the_page(base_queryset(user), params or {})
        .filter(status=CASE_STATUS[WIRE_EXCLUSION])
        # `wire` is Postgres `json`, which has no equality operator, so it
        # cannot be grouped on directly -- the cast is what makes GROUP BY
        # legal, and the text is what the driver would have sent anyway.
        .annotate(wire_text=Cast("wire", TextField()))
        .values("wire_text")
        .annotate(rows_holding_it=Count("*"))
    )
    counts: Counter = Counter()
    for row in rows:
        try:
            value = json.loads(row["wire_text"]) if row["wire_text"] else None
        except ValueError:
            continue
        for name in service_names(value):
            counts[name] += row["rows_holding_it"]
    return [name for name, _count in counts.most_common(limit)]


def _vocab_key(user, params):
    """Per reader, per scope. Two people may be granted different
    datasets, so a vocabulary built for one is not shown to the other."""
    import hashlib

    scope = "&".join(f"{key}={params.get(key, '')}" for key in _VOCAB_SCOPES)
    digest = hashlib.sha256(scope.encode()).hexdigest()[:16]
    return f"review.vocab.{user.pk}.{digest}"


def vocab(user, params=None):
    """Filter vocabularies read from the data, or None when the crawler
    database is not reachable.

    Scoped to the page's dataset and window, and held for
    VOCAB_CACHE_SECONDS. Both for the same reason: these are read from
    the corpus so that every value offered returns rows, and reading the
    whole corpus for them cost more than the page's own queries once
    those were fixed.
    """
    from django.core.cache import cache
    from django.db import DatabaseError

    from explorer.dberrors import absent_or_raise
    from explorer.models import Dataset

    params = params or {}
    key = _vocab_key(user, params)
    held = cache.get(key)
    if held is not None:
        return held

    try:
        built = {
            # NOT scoped: this is the picker that changes the scope, so
            # narrowing it to the current dataset would offer one option.
            "datasets": list(Dataset.objects.order_by("label").values("slug", "label")),
            "labels": sorted(
                _scoped_to_the_page(base_queryset(user), params)
                .filter(primary_label__isnull=False)
                .values_list("primary_label", flat=True)
                .distinct()
            ),
            # Derived from the queue itself, so every value offered
            # returns rows — and removed_in_march_review, which the queue
            # never holds, is never offered as a filter.
            "skip_reasons": sorted(
                value
                for value in _scoped_to_the_page(base_queryset(user), params)
                .values_list("enrichment__skip_reason", flat=True)
                .distinct()
                if value
            ),
            # The syndications the wire case can be worked one at a time.
            # Flattened from `articles.wire`, which is a JSON array: one
            # article can name several, and each of them is a separate
            # thing to be right or wrong about.
            "services": _wire_services(user, params),
            # The rules that decided, most-used first. A method is what a
            # precision figure attaches to; a syndication is not.
            "methods": WIRE_METHODS,
        }
    except DatabaseError as exc:
        # A missing crawler database is "not connected"; a query this
        # repository got wrong is not, and used to be reported as one.
        absent_or_raise(exc, "review.queue.vocab")
        return None

    # Not held when the crawler is unreachable: `None` means "ask again",
    # and caching it would keep the page saying "not connected" for five
    # minutes after the database came back.
    with contextlib.suppress(Exception):
        cache.set(key, built, VOCAB_CACHE_SECONDS)
    return built
