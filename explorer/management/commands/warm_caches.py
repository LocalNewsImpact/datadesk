"""Fill the dashboard's caches so no one waits for them.

Every expensive read on the dashboard is cached, but a cache only helps
the second reader. The first pays the full cost, and with a shared cache
that means one person every time an entry expires — measured between ten
and twenty-seven seconds depending on how much of the corpus Postgres
happens to be holding in memory.

Run from the deploy, so a new revision serves a warm cache from its
first request, and on a schedule if one exists. Failures are reported
and do not stop the caller: a cold cache is slow, not broken, and a
deploy should not fail because the crawler database was briefly
unreachable.
"""

import time

from django.core.management.base import BaseCommand


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

        from explorer.costs import recorded_costs
        from explorer.crawler import dataset_row_counts
        from explorer.dashboard import corpus_summary

        targets = (
            ("dataset row counts", "explorer.dataset_row_counts", dataset_row_counts),
            ("recorded costs", "explorer.recorded_costs", recorded_costs),
            ("corpus summary", "explorer.corpus_summary", corpus_summary),
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
