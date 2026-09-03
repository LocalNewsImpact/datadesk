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

from django.db.models import Case as SQLCase
from django.db.models import Count, F, IntegerField, Q, TextField, Value, When
from django.db.models.functions import Coalesce, Length

from accounts.privileges import WRITE
from explorer.models import Article, DatasetSource
from explorer.scoping import narrow

PAYWALL_STUB = "paywall_stub"
MINIMAL_CAPTURE = "minimal_capture"
SCOPE_MISLABEL = "scope_mislabel"

# article_enrichment.skip_reason, as production actually holds it. Two
# spellings mean the same thing: the bulk March update wrote
# paywall_stub_exported_unenriched, and the live pipeline writes
# paywall_stub (src/enrichment/orchestrator.py,
# _GATE_VERDICT_SKIP_REASON). Both belong in the queue.
PAYWALL_STUB_SKIP_REASONS = ("paywall_stub", "paywall_stub_exported_unenriched")

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


def evidence_is_corroborated(evidence) -> bool:
    """Did anything beyond a phrase in the body agree with the verdict?"""
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
}

CASE_LABELS = {
    PAYWALL_STUB: "Paywall stubs",
    MINIMAL_CAPTURE: "Minimal or empty captures",
    SCOPE_MISLABEL: "Scope mislabels",
}

CASE_NOTES = {
    PAYWALL_STUB: (
        "Text is a teaser or a login wall. The CIN label and byline are "
        "still usable, so exclusion loses valid observations."
    ),
    MINIMAL_CAPTURE: (
        "Genuine boilerplate and real articles whose text never arrived, "
        "mixed together. Check the long bands first."
    ),
    SCOPE_MISLABEL: (
        "Scope-excluded but kept for export, scope recorded. In March "
        "roughly 70% were locally bylined stories that merely referenced "
        "a foreign subject."
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
            # SCOPE.md §2.3 names an `out_of_scope` status for these, while
            # production carries all 69 as skip reasons on
            # enrichment_skipped. The status is kept as an extra selector
            # rather than dropped: if such a row exists it belongs here by
            # the section's own rule that no automated step may exclude an
            # article carrying a CIN label, and it cannot pull in the
            # human removals, which _flagged_q excludes unconditionally.
            # Delete this line once the status is confirmed unused.
            | Q(status="out_of_scope")
        )
    if case == MINIMAL_CAPTURE:
        return Q(status=CASE_STATUS[MINIMAL_CAPTURE])
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
            text_length=Length(
                Coalesce(
                    "content",
                    "text",
                    "text_excerpt",
                    Value(""),
                    output_field=TextField(),
                )
            ),
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


def _apply_common(qs, params):
    """Filters shared by the queue and its facet counts."""
    # An unrecognized case reads as no case filter rather than an error.
    if (case := params.get("case")) and case in CASE_STATUS:
        qs = qs.filter(_case_q(case))
    if slug := params.get("dataset"):
        member_sources = DatasetSource.objects.filter(dataset__slug=slug).values(
            "source_id"
        )
        qs = qs.filter(candidate_link__source_id__in=member_sources)
    if publisher := params.get("publisher"):
        # Publishers are searched by name: a hostname is not an
        # identifier and must not be matched on (it changes, and
        # the same one can front two records).
        qs = qs.filter(candidate_link__source__canonical_name__icontains=publisher)
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


def queued(params, user):
    """The queue itself: longest captures first.

    Length descending is the useful default — the wrongly flagged
    articles are the long ones, and putting them on the first page is the
    point of the queue.
    """
    qs = _apply_common(base_queryset(user), params)
    band = params.get("band")
    if band in BAND_BOUNDS:
        qs = _apply_band(qs, band)
    return qs.order_by("-text_length", "-created_at")


def band_facets(params, user):
    """Counts per length band, ignoring any band already selected.

    A facet that counted only the selected band would always read as the
    result count and tell the operator nothing.
    """
    qs = _apply_common(base_queryset(user), params)
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
    qs = _apply_common(base_queryset(user), scoped)
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


def vocab(user):
    """Filter vocabularies read from the data, or None when the crawler
    database is not reachable."""
    from django.db import DatabaseError

    from explorer.models import Dataset

    try:
        return {
            "datasets": list(Dataset.objects.order_by("label").values("slug", "label")),
            "labels": sorted(
                Article.objects.filter(primary_label__isnull=False)
                .values_list("primary_label", flat=True)
                .distinct()
            ),
            # Derived from the queue itself, so every value offered
            # returns rows — and removed_in_march_review, which the queue
            # never holds, is never offered as a filter.
            "skip_reasons": sorted(
                value
                for value in base_queryset(user)
                .values_list("enrichment__skip_reason", flat=True)
                .distinct()
                if value
            ),
        }
    except DatabaseError:
        return None
