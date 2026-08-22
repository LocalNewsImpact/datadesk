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

from explorer.models import Article, DatasetSource

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


def base_queryset():
    """Flagged articles, annotated with what the operator has to judge:
    how much text was captured, the reason the gate gave, the CIN label
    and whether a byline survived."""
    return (
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
        )
        .filter(_flagged_q())
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


def queued(params):
    """The queue itself: longest captures first.

    Length descending is the useful default — the wrongly flagged
    articles are the long ones, and putting them on the first page is the
    point of the queue.
    """
    qs = _apply_common(base_queryset(), params)
    band = params.get("band")
    if band in BAND_BOUNDS:
        qs = _apply_band(qs, band)
    return qs.order_by("-text_length", "-created_at")


def band_facets(params):
    """Counts per length band, ignoring any band already selected.

    A facet that counted only the selected band would always read as the
    result count and tell the operator nothing.
    """
    qs = _apply_common(base_queryset(), params)
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


def case_facets(params):
    """Counts per case, ignoring any case already selected."""
    # .copy() rather than dict(): a QueryDict's dict() flattens to lists.
    scoped = params.copy()
    scoped.pop("case", None)
    qs = _apply_common(base_queryset(), scoped)
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


def vocab():
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
                for value in base_queryset()
                .values_list("enrichment__skip_reason", flat=True)
                .distinct()
                if value
            ),
        }
    except DatabaseError:
        return None
