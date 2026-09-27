"""Load the outlet registry file into the `outlet_registry` table.

    python manage.py import_outlet_registry [path-or-url]

Defaults to the crawler repo's published src/lookups/mo_outlet_registry.csv on
GitHub main. Run after each rebuild of the registry is merged there.
"""

from django.core.management.base import BaseCommand

from visuals.outlets import DEFAULT_URL, import_registry


class Command(BaseCommand):
    help = "Load the outlet registry CSV into the outlet_registry table."

    def add_arguments(self, parser):
        parser.add_argument("where", nargs="?", default=DEFAULT_URL)

    def handle(self, *args, **options):
        counts = import_registry(options["where"])
        self.stdout.write(
            "outlet registry: {rows} rows, {created} created, {updated} updated, "
            "{removed} removed".format(**counts)
        )
