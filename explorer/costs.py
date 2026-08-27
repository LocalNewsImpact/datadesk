"""The cost dashboard's queries (SCOPE.md §2.6).

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

_CACHE_SECONDS = 3600


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

        # One pass over enrichment grouped by source, then added up per
        # dataset in Python.
        #
        # This was an aggregate per dataset, each one traversing
        # enrichment -> article -> candidate_link over the whole corpus.
        # Four datasets meant four full traversals, and the cost grew
        # with every dataset added. Summing in Python is correct here
        # because a source may belong to several datasets and should
        # count in each.
        per_source = {
            row["article__candidate_link__source_id"]: row
            for row in enriched.values("article__candidate_link__source_id").annotate(
                cost=Sum("cost_usd"), articles=Count("article_id")
            )
        }
        members = {}
        for dataset_id, source_id in DatasetSource.objects.values_list(
            "dataset_id", "source_id"
        ):
            members.setdefault(dataset_id, []).append(source_id)

        by_dataset = []
        for dataset in Dataset.objects.order_by("label"):
            rows = [
                per_source[s] for s in members.get(dataset.id, ()) if s in per_source
            ]
            by_dataset.append(
                {
                    "label": dataset.label,
                    "slug": dataset.slug,
                    "cost": sum(r["cost"] for r in rows) or 0,
                    "articles": sum(r["articles"] for r in rows),
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


# The trace is one JSON string per row, not columns. Every field below is
# read out of it by path, which is what the previous version of this query
# got wrong: it named columns -- created_at, usage, cache_discount -- that
# do not exist, BigQuery rejected it with "Unrecognized name: created_at",
# and `billed_costs()` swallowed the error and returned None. The dashboard
# has been showing the recorded side alone, without saying so, since it was
# written.
#
# `usage` is the net charge, not list price. Checked against a trace on
# 2026-08-21: inputCost + outputCost == usage exactly, and inputCost is
# already below unit price x tokens because the cached prompt tokens are
# billed at about a tenth. `usage_cache` is a negative savings line that
# says what the cache was worth -- informational, and subtracting it would
# discount the bill twice.
#
# `external_user` is what LiteLLM's `user=` becomes on the OpenRouter side.
# Nothing sets it yet, so billed cost cannot be split per dataset; the
# column is read anyway so that it starts working the day the crawler
# passes one, rather than needing this query changed again.
_BILLED_SQL = """
    WITH t AS (
      SELECT
        TIMESTAMP(JSON_VALUE(trace, '$.timestamp')) AS at,
        CAST(JSON_VALUE(trace, '$.metadata.openrouter_generation.usage')
             AS FLOAT64) AS usage,
        CAST(JSON_VALUE(trace, '$.metadata.openrouter_generation.usage_cache')
             AS FLOAT64) AS usage_cache,
        JSON_VALUE(trace, '$.metadata.openrouter_generation.model') AS model,
        JSON_VALUE(trace, '$.metadata.openrouter_generation.external_user')
          AS dataset
      FROM `mizzou-news-crawler.mizzou_analytics.openrouter_traces`
    )
    SELECT
      DATE(at) AS day,
      SUM(usage) AS billed,
      SUM(usage_cache) AS cache_discount,
      COUNT(*) AS requests,
      COUNTIF(usage_cache <> 0) AS cached_requests
    FROM t
    WHERE at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
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
    except Exception as exc:
        # Said, not swallowed. This returned None for every failure alike,
        # so a query that had been broken since it was written looked
        # exactly like "BigQuery is not configured here" -- and the page
        # showed recorded cost under a heading that promised both.
        #
        # The reason goes on the page. A number that is missing is a fact
        # about the number; a number that is missing for a reason nobody
        # can see is a fact about nothing.
        return {"unavailable": str(exc)[:300]}
