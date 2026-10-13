"""Add events read from the press to the review queue.

    python manage.py import_history_event_candidates <path-or-url> \
        --publication "Missouri Press News"

The file is the reading's events.csv: one row per event, with its page,
quote and how its paper was matched (docs/OUTLET_EVENTS.md). A reading
already held is skipped, so a file can be loaded more than once; a file with
any bad row adds nothing and says which lines. Nothing here is history until
a person accepts it.
"""

from django.core.management.base import BaseCommand, CommandError

from visuals.outlet_events import read_rows
from visuals.press_history import load, refresh


class Command(BaseCommand):
    help = "Add events read from the press to the review queue."

    def add_arguments(self, parser):
        parser.add_argument("where")
        parser.add_argument(
            "--publication",
            required=True,
            help='The publication they were read from, e.g. "Missouri Press News".',
        )
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="a later reading of the same pages: update the candidates still "
            "waiting, add new ones, remove those it no longer holds; decided "
            "candidates are never touched",
        )

    def handle(self, *args, **options):
        if options["refresh"]:
            counts, errors, held = refresh(
                read_rows(options["where"]), options["publication"]
            )
            if errors:
                raise CommandError("nothing changed:\n" + "\n".join(errors))
            self.stdout.write(
                "history event candidates: "
                + ", ".join(f"{n} {k}" for k, n in counts.items())
            )
            for line in held:
                self.stdout.write(f"  not added, already decided: {line}")
            return
        added, skipped, errors = load(
            read_rows(options["where"]), options["publication"]
        )
        if errors:
            raise CommandError("nothing added:\n" + "\n".join(errors))
        self.stdout.write(
            f"history event candidates: {added} added, {skipped} already held"
        )
