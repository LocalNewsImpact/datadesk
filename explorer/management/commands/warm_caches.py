"""Fill the caches behind the dashboard and the costs page.

Every expensive read on those pages is cached, but a cache only helps
the second reader. The first pays the full cost, and with a shared cache
that means one person every time an entry expires — measured between ten
and twenty-seven seconds depending on how much of the corpus Postgres
happens to be holding in memory, and 110 seconds for the BigQuery read
behind the costs page.

Every cache on those two pages belongs in `targets` below. One that does
not is worse than one that is not cached at all: the page looks fast
because its neighbours are warm, and the cost lands on whoever happens
to arrive after the entry expires.

Run from the deploy, so a new revision serves a warm cache from its
first request, and on a schedule if one exists. Failures are reported
and do not stop the caller: a cold cache is slow, not broken, and a
deploy should not fail because the crawler database was briefly
unreachable.
"""

import time

from django.core.management.base import BaseCommand


def _newsroom_count_targets():
    """Article counts per newsroom, one entry per dataset and one for all.

    The builder's newsroom step draws its tree from `sources`, which is
    cheap, and hangs these counts beside the names. Counting them is an
    aggregate over the whole corpus, and it took that step to 24 seconds
    when it ran inline.

    Warmed here because the cost is now paid by whoever opens the step
    first after a sync rather than by everybody -- and being first should
    not be a punishment. Bounded by the number of datasets, so this stays
    a handful of queries however many visuals exist.

    Facet values are deliberately not warmed. There is one per dimension
    per slice, which is unbounded, and they now sit behind a disclosure
    somebody has to open -- so nobody waits on them to see a page.
    """

    from explorer.models import Dataset
    from visuals.corpus import _cache_key
    from visuals.views import newsroom_counts_for, newsroom_tree_for

    try:
        slugs = sorted(Dataset.objects.values_list("slug", flat=True))
    except Exception:  # noqa: BLE001 — a cold cache is slow, not broken
        return ()

    targets = []
    for scopes in [[]] + [[slug] for slug in slugs]:
        label = f"newsroom counts ({scopes[0] if scopes else 'all datasets'})"
        key = _cache_key("visuals.newsroom_counts", scopes)
        targets.append((label, key, lambda s=scopes: newsroom_counts_for(s)))
        # The tree beside the counts. It is the more expensive of the two
        # -- every source in every dataset, arranged -- and the facet
        # cascade reads it as well as the newsroom step, so an unwarmed
        # tree is two screens waiting rather than one.
        targets.append(
            (
                f"newsroom tree ({scopes[0] if scopes else 'all datasets'})",
                _cache_key("visuals.newsroom_tree", scopes),
                lambda s=scopes: newsroom_tree_for(s),
            )
        )
    return tuple(targets)


class Command(BaseCommand):
    help = "Recompute the dashboard's cached reads."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Recompute even if an entry is still warm.",
        )

    def handle(self, *args, **options):
        from django.core.cache import cache

        from explorer.costs import billed_costs, recorded_costs
        from explorer.crawler import dataset_row_counts
        from explorer.dashboard import corpus_summary
        from review.views import (
            PAYWALL_COUNTS_CACHE_KEY,
            paywall_corpus_counts,
        )

        targets = (
            ("dataset row counts", "explorer.dataset_row_counts", dataset_row_counts),
            ("recorded costs", "explorer.recorded_costs", recorded_costs),
            ("corpus summary", "explorer.corpus_summary", corpus_summary),
            # The paywalls page's ranking: every enrichment row joined
            # through articles and candidate links, 13s cold against
            # production. It is a working surface -- somebody saves a price
            # and returns to the list -- so an unwarmed cache is paid on
            # arrival and again after every five-minute lapse.
            (
                "paywall corpus counts",
                PAYWALL_COUNTS_CACHE_KEY,
                paywall_corpus_counts,
            ),
            # The most expensive of the four and the last to be warmed: it
            # goes to BigQuery rather than Postgres, and thirty days of
            # openrouter_traces took 110s uncached on 2026-08-23. It was
            # missing from this list while every other cache on the page was
            # warm, so /explorer/costs/ paid for it on whoever arrived first
            # after an entry expired.
            ("billed costs", "explorer.billed_costs", billed_costs),
            *_newsroom_count_targets(),
        )

        failures = 0
        for label, key, fetch in targets:
            if options["force"]:
                cache.delete(key)
            started = time.perf_counter()
            try:
                fetch()
            except Exception as exc:  # noqa: BLE001 — reported, never fatal
                failures += 1
                self.stderr.write(self.style.WARNING(f"{label}: {exc}"))
                continue
            elapsed = time.perf_counter() - started

            # Ask the cache, rather than trusting that the call returned.
            #
            # Each of these swallows a DatabaseError and returns None so
            # the dashboard degrades to one banner instead of five empty
            # panels. That means a call can "succeed" having cached
            # nothing at all — which is exactly what happened when this
            # first ran from a job with no crawler credentials, and it
            # reported every entry warm while the cache table stayed
            # empty.
            if cache.get(key) is None:
                failures += 1
                self.stderr.write(
                    self.style.WARNING(
                        f"{label}: returned nothing to cache after "
                        f"{elapsed:.2f}s — is the crawler database reachable?"
                    )
                )
                continue
            self.stdout.write(f"{label}: {elapsed:.2f}s")

        if failures:
            self.stdout.write(
                self.style.WARNING(f"{failures} of {len(targets)} could not be warmed")
            )
        else:
            self.stdout.write(self.style.SUCCESS("caches warm"))
