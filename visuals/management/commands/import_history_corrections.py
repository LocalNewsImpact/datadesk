"""Record newspaper-history corrections from a CSV: the backfill of the
corrections the history's build once kept in its own files.

    python manage.py import_history_corrections path-or-url --by someone@example.org

Columns are a correction's fields (visuals.history_corrections.FIELDS). A row
identical to a correction in force is skipped, so a file can be loaded more
than once; a file with any bad row writes nothing and says which lines.
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from visuals.history_corrections import load
from visuals.outlet_events import read_rows


class Command(BaseCommand):
    help = "Record newspaper-history corrections from a CSV."

    def add_arguments(self, parser):
        parser.add_argument("where")
        parser.add_argument(
            "--by", required=True, help="Email of the person the corrections are from."
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
        self.stdout.write(f"recorded {recorded}, already recorded {skipped}")
