"""Record outlet events from a CSV: the backfill of what already happened.

    python manage.py import_outlet_events [path-or-url] --by someone@example.org

Defaults to visuals/data/outlet_events_seed.csv, the events reported in the
Missouri Press Association's stories. Columns are the event's fields
(docs/OUTLET_EVENTS.md). A row identical to an event already recorded is
skipped, so a file can be loaded more than once; a file with any bad row
writes nothing and says which lines.
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from visuals.outlet_events import load, read_rows

SEED = Path(__file__).resolve().parents[2] / "data" / "outlet_events_seed.csv"


class Command(BaseCommand):
    help = "Record outlet events from a CSV."

    def add_arguments(self, parser):
        parser.add_argument("where", nargs="?", default=str(SEED))
        parser.add_argument(
            "--by", required=True, help="Email of the person the events are from."
        )

    def handle(self, *args, **options):
        actor = get_user_model().objects.filter(email__iexact=options["by"]).first()
        if actor is None:
            raise CommandError(f"No user with email {options['by']}")
        where = options["where"]
        recorded, skipped, errors = load(
            actor, read_rows(where), origin=f"backfill: {Path(where).name}"
        )
        if errors:
            raise CommandError("nothing recorded:\n" + "\n".join(errors))
        self.stdout.write(
            f"outlet events: {recorded} recorded, {skipped} already recorded"
        )
