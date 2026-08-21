"""The cost dashboard's queries (SCOPE.md §2.5).

Two sources joined by day:

- recorded: article_enrichment.cost_usd in the crawler Postgres — what
  the pipeline believed each article cost when it ran
- billed: openrouter_traces in BigQuery — what OpenRouter actually
  charged, including the cache discount

The standing headline is recorded vs billed. Alerts are out of scope for
v1. Everything is cached: the numbers move nightly, not per-request.
"""

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate

from explorer.models import ArticleEnrichment, Dataset, DatasetSource

_CACHE_SECONDS = 300


def recorded_costs():
    """Recorded-cost rollups from the crawler DB, or None when offline."""

    def fetch():
        enriched = ArticleEnrichment.objects.filter(cost_usd__isnull=False)
        totals = enriched.aggregate(
            total=Sum("cost_usd"), articles=Count("article_id"), avg=Avg("cost_usd")
        )

        by_day = list(
            enriched.filter(enriched_at__isnull=False)
            .annotate(day=TruncDate("enriched_at"))
            .values("day")
            .annotate(cost=Sum("cost_usd"), articles=Count("article_id"))
            .order_by("-day")[:30]
        )

        by_dataset = []
        for dataset in Dataset.objects.order_by("label"):
            member_sources = DatasetSource.objects.filter(dataset=dataset).values(
                "source_id"
            )
            row = enriched.filter(
                article__candidate_link__source_id__in=member_sources
            ).aggregate(cost=Sum("cost_usd"), articles=Count("article_id"))
            by_dataset.append(
                {
                    "label": dataset.label,
                    "slug": dataset.slug,
                    "cost": row["cost"] or 0,
                    "articles": row["articles"],
                }
            )

        by_model = list(
            enriched.values("model")
            .annotate(cost=Sum("cost_usd"), articles=Count("article_id"))
            .order_by("-cost")
        )

        return {
            "totals": totals,
            "by_day": by_day,
            "by_dataset": by_dataset,
            "by_model": by_model,
        }

    try:
        return cache.get_or_set("explorer.recorded_costs", fetch, _CACHE_SECONDS)
    except DatabaseError:
        return None


# NOTE: field names follow OpenRouter's generation log schema (usage,
# cache_discount, tokens_prompt/tokens_completion, provider_name) as the
# external table maps it. True this up against the real openrouter_traces
# columns on the first run against BigQuery — the query lives only here.
_BILLED_SQL = """
    SELECT
      DATE(created_at) AS day,
      SUM(usage) AS billed,
      SUM(cache_discount) AS cache_discount,
      COUNT(*) AS requests,
      COUNTIF(cache_discount > 0) AS cached_requests
    FROM `mizzou-news-crawler.mizzou_analytics.openrouter_traces`
    WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    GROUP BY day
    ORDER BY day DESC
"""


def billed_costs():
    """Billed-cost rollups from openrouter_traces, or None when offline."""

    def fetch():
        from explorer.analytics import query_rows

        rows = query_rows(_BILLED_SQL)
        total = sum(r["billed"] or 0 for r in rows)
        discount = sum(r["cache_discount"] or 0 for r in rows)
        requests = sum(r["requests"] or 0 for r in rows)
        cached = sum(r["cached_requests"] or 0 for r in rows)
        return {
            "by_day": rows,
            "total": total,
            "cache_discount": discount,
            "requests": requests,
            "cache_hit_rate": (cached / requests) if requests else None,
        }

    try:
        cached_value = cache.get("explorer.billed_costs")
        if cached_value is not None:
            return cached_value
        value = fetch()
        cache.set("explorer.billed_costs", value, _CACHE_SECONDS)
        return value
    except Exception:
        # BigQuery unreachable, credentials absent, or the table's columns
        # differ — the dashboard renders the recorded side and says so.
        return None
