"""Corpus-wide counts for the landing dashboard.

One cached read of the crawler database behind the landing page: what the
corpus holds by status, how much of it enrichment reached, and what that
cost per dataset. Nightly-fresh is the contract (SCOPE.md §5), so none of
this is recomputed per request.

Returns None — not an empty summary — when the crawler database is not
reachable, so the page can say "not configured" instead of showing zeros
that look like an empty corpus (SCOPE.md §6 leaves the read-only role an
open Phase 0 item; local development runs without it).
"""

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Count

from explorer.models import Article, ArticleEnrichment

_CACHE_KEY = "explorer.corpus_summary"
_CACHE_SECONDS = 3600

# Statuses in pipeline order, with what each one means. The vocabulary is
# the pipeline's; anything the data holds that is not listed here still
# appears, after these, under its own name (SCOPE.md §2.2).
STATUS_NOTES = {
    "enriched": "Enrichment ran and recorded a result",
    "enrichment_skipped": "Exported without enrichment — CIN label and byline intact",
    "labeled": "CIN label assigned, awaiting enrichment",
    "not_article": "Triage judged the capture unusable",
    "out_of_scope": "Legacy automated scope exclusion",
}
STATUS_ORDER = list(STATUS_NOTES)


def _percent(part, whole):
    return (100.0 * part / whole) if whole else None


def corpus_summary():
    """Status counts, enrichment coverage and the review backlog, or None."""

    def fetch():
        from review.queue import queued

        rows = {
            row["status"]: row["articles"]
            for row in Article.objects.values("status").annotate(articles=Count("id"))
        }
        total = sum(rows.values())

        ordered = [status for status in STATUS_ORDER if status in rows]
        ordered += sorted(status for status in rows if status not in STATUS_NOTES)
        by_status = [
            {
                "status": status,
                "articles": rows[status],
                "note": STATUS_NOTES.get(status, ""),
                "share": _percent(rows[status], total),
            }
            for status in ordered
        ]

        enriched = rows.get("enriched", 0)
        # Enrichment rows can outnumber `enriched` articles: a skipped
        # article still gets a row carrying its skip reason.
        enrichment_rows = ArticleEnrichment.objects.count()
        with_claim = ArticleEnrichment.objects.filter(point_geoid__isnull=False).count()

        return {
            "total": total,
            "by_status": by_status,
            "enriched": enriched,
            "coverage": _percent(enriched, total),
            "exported_unenriched": rows.get("enrichment_skipped", 0),
            "enrichment_rows": enrichment_rows,
            "with_claim": with_claim,
            "claim_share": _percent(with_claim, enrichment_rows),
            "flagged": queued({}).count(),
        }

    try:
        return cache.get_or_set(_CACHE_KEY, fetch, _CACHE_SECONDS)
    except DatabaseError:
        return None


def datasets_table(row_counts, recorded):
    """Per-dataset articles beside recorded enrichment cost.

    Two cached reads joined on slug: `row_counts` from
    explorer.crawler.dataset_row_counts, `recorded` from
    explorer.costs.recorded_costs. Either may be absent; a dataset with
    no recorded cost still lists, at zero.
    """
    if not row_counts:
        return []
    costs = {row["slug"]: row for row in (recorded or {}).get("by_dataset", []) or []}
    table = []
    for row in row_counts:
        cost_row = costs.get(row["slug"], {})
        table.append(
            {
                "slug": row["slug"],
                "label": row["label"],
                "articles": row["articles"],
                "enriched": cost_row.get("articles", 0),
                "cost": cost_row.get("cost", 0),
            }
        )
    return table
