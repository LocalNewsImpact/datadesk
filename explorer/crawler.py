"""Read-only queries against the crawler's database (SCOPE.md §1).

Raw SQL over the `crawler` connection alias, joined the way the crawler's
own enrichment repository joins (articles → candidate_links →
dataset_sources → datasets). Results are cached: the corpus is millions of
rows, nightly-fresh is the contract (SCOPE.md §5), and the landing page
must not run a COUNT over it on every request.
"""

from django.core.cache import cache
from django.db import DatabaseError, connections

from explorer.dberrors import absent_or_raise

_COUNTS_CACHE_KEY = "explorer.dataset_row_counts"
_COUNTS_CACHE_SECONDS = 3600

# LEFT JOINs so a dataset with no sources or no articles still appears,
# at zero, rather than vanishing. DISTINCT because a source can belong to
# more than one dataset and the join fans out.
# Count per source once, then add the sources up per dataset.
#
# Counting DISTINCT article ids across the whole three-way join made
# Postgres sort every joined row before it could group them: 261,531
# rows, spilled 29MB to disk, to produce one number per dataset. The
# aggregate here runs over articles alone and the join that follows
# carries four rows, not a quarter of a million.
#
# SUM is safe where COUNT(DISTINCT) was needed because (dataset_id,
# source_id) is unique, so no dataset counts a source twice, and each
# article has exactly one candidate link. Verified against the old
# query: identical counts for every dataset.
_DATASET_ROW_COUNTS_SQL = """
    WITH per_source AS (
        SELECT cl.source_id, COUNT(*) AS articles
        FROM articles a
        JOIN candidate_links cl ON cl.id = a.candidate_link_id
        GROUP BY cl.source_id
    )
    SELECT d.slug, d.label, COALESCE(SUM(ps.articles), 0) AS articles
    FROM datasets d
    LEFT JOIN dataset_sources ds ON ds.dataset_id = d.id
    LEFT JOIN per_source ps ON ps.source_id = ds.source_id
    GROUP BY d.id, d.slug, d.label
    ORDER BY d.label
"""


def dataset_row_counts():
    """Article counts per dataset, or None when the crawler DB is not there.

    None (connection refused, tables absent — the local sqlite fallback)
    is distinct from an empty list (connected, no datasets); the landing
    page renders the two differently.
    """

    def fetch():
        with connections["crawler"].cursor() as cursor:
            cursor.execute(_DATASET_ROW_COUNTS_SQL)
            return [
                {"slug": slug, "label": label, "articles": articles}
                for slug, label, articles in cursor.fetchall()
            ]

    try:
        return cache.get_or_set(_COUNTS_CACHE_KEY, fetch, _COUNTS_CACHE_SECONDS)
    except DatabaseError as exc:
        absent_or_raise(exc, "explorer.crawler.counts")
        return None
