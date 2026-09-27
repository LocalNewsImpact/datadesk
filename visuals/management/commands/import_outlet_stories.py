"""Add stories to the outlet stories list from a CSV.

    python manage.py import_outlet_stories [path] --by someone@example.org

Defaults to visuals/data/outlet_stories_seed.csv, the 55 Missouri Press
Association stories scanned in September 2026. The file is in the table's
own shape: Date, Headline as [text](link), Type, Publications, Owners,
Paragraph. A story whose link is already listed is skipped; a file with any
bad row adds nothing and says which lines.
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from visuals.outlet_stories import load, read_csv

SEED = Path(__file__).resolve().parents[2] / "data" / "outlet_stories_seed.csv"


class Command(BaseCommand):
    help = "Add stories to the outlet stories list from a CSV."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default=str(SEED))
        parser.add_argument("--by", required=True, help="Email of who is adding them.")

    def handle(self, *args, **options):
        actor = get_user_model().objects.filter(email__iexact=options["by"]).first()
        if actor is None:
            raise CommandError(f"No user with email {options['by']}")
        added, skipped, errors = load(actor, read_csv(options["path"]))
        if errors:
            raise CommandError("nothing added:\n" + "\n".join(errors))
        self.stdout.write(f"outlet stories: {added} added, {skipped} already listed")
