"""Record broadcast stations from a reviewed sheet.

    python manage.py import_broadcast_stations path --by someone@example.org

Columns are a station's fields (visuals.broadcast.FIELDS). A station is keyed
by its FCC facility id: loading a sheet again updates it. A sheet with any bad
row writes nothing and says which lines.
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from visuals.broadcast import load
from visuals.outlet_events import read_rows


class Command(BaseCommand):
    help = "Record broadcast stations from a reviewed sheet."

    def add_arguments(self, parser):
        parser.add_argument("where")
        parser.add_argument(
            "--by", required=True, help="Email of the person the stations are from."
        )

    def handle(self, *args, **options):
        actor = get_user_model().objects.filter(email__iexact=options["by"]).first()
        if actor is None:
            raise CommandError(f"No user with email {options['by']}")
        where = options["where"]
        written, errors = load(
            actor, read_rows(where), origin=f"backfill: {Path(where).name}"
        )
        if errors:
            raise CommandError("nothing recorded:\n" + "\n".join(errors))
        self.stdout.write(f"stations recorded: {written}")
