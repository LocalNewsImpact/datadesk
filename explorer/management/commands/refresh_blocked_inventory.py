"""Count what is blocked, out of band, so the page never has to.

The Blocked report's twenty-one questions cost about 97 seconds against
production. Run from a request that is a 504; run from here it is one
task of `daily_housekeeping`, writing a row the page renders in
milliseconds.

    python manage.py refresh_blocked_inventory

Reads the crawler as `datadesk_ro` and writes only Datadesk's own table.
"""

import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from explorer import blocked
from explorer.models import BlockedInventory

#: Snapshots older than this are dropped. Kept at all because whether
#: "never fetched" is growing is a more useful question than what it is
#: now; pruned so the table stays a month of daily rows.
KEEP_DAYS = 30


class Command(BaseCommand):
    help = "Count what is blocked and store it for the Blocked page"

    def add_arguments(self, parser):
        parser.add_argument(
            "--prune-only",
            action="store_true",
            help="Drop old snapshots and write none",
        )

    def handle(self, *args, **options):
        if not options["prune_only"]:
            started = time.monotonic()
            rows = blocked.inventory()
            took_ms = int((time.monotonic() - started) * 1000)

            # None means the crawler was unreachable. Writing an empty
            # snapshot would replace a good answer with a blank one and
            # the page would show zero blockages, which is a worse lie
            # than an hour-old number.
            if rows is None:
                self.stderr.write("crawler unreachable; nothing written")
                return

            snapshot = BlockedInventory.objects.create(rows=rows, took_ms=took_ms)
            total = sum(r["count"] for r in rows)
            self.stdout.write(
                f"counted {len(rows)} checks, {total} rows blocked, "
                f"in {took_ms / 1000:.1f}s (snapshot {snapshot.pk})"
            )

        cutoff = timezone.now() - timezone.timedelta(days=KEEP_DAYS)
        dropped, _ = BlockedInventory.objects.filter(computed_at__lt=cutoff).delete()
        if dropped:
            self.stdout.write(f"pruned {dropped} snapshots older than {KEEP_DAYS}d")
