"""Read-only queries against the crawler's database (SCOPE.md §1).

Raw SQL over the `crawler` connection alias, joined the way the crawler's
own enrichment repository joins (articles → candidate_links →
dataset_sources → datasets). Results are cached: the corpus is millions of
rows, nightly-fresh is the contract (SCOPE.md §5), and the landing page
must not run a COUNT over it on every request.
"""

from django.core.cache import cache
from django.db import DatabaseError, connections

_COUNTS_CACHE_KEY = "explorer.dataset_row_counts"
_COUNTS_CACHE_SECONDS = 300

# LEFT JOINs so a dataset with no sources or no articles still appears,
# at zero, rather than vanishing. DISTINCT because a source can belong to
# more than one dataset and the join fans out.
_DATASET_ROW_COUNTS_SQL = """
    SELECT d.slug, d.label, COUNT(DISTINCT a.id) AS articles
    FROM datasets d
    LEFT JOIN dataset_sources ds ON ds.dataset_id = d.id
    LEFT JOIN candidate_links cl ON cl.source_id = ds.source_id
    LEFT JOIN articles a ON a.candidate_link_id = cl.id
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
    except DatabaseError:
        return None
