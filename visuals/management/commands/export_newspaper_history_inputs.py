"""Write the newspaper history's reviewer inputs from datadesk's tables.

    python manage.py export_newspaper_history_inputs <folder>

The history's build (the crawler repo's scripts/newspaper_history) reads
these files; they are made from the tables here before every build and are
never edited by hand. See docs/NEWSPAPER_HISTORY.md.
"""

from django.core.management.base import BaseCommand

from visuals.history_corrections import export


class Command(BaseCommand):
    help = "Write the newspaper history's reviewer inputs from datadesk's tables."

    def add_arguments(self, parser):
        parser.add_argument("folder")

    def handle(self, *args, **options):
        for name, n in export(options["folder"]).items():
            self.stdout.write(f"{name}: {n} rows")
