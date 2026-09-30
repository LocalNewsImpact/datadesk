"""Fetch the Census layers a map can shade by, for one geography level.

    CENSUS_API_KEY=... python manage.py fetch_census_layers --level county
    python manage.py fetch_census_layers --level tract --report

Stores every variable in `visuals.census.VARIABLES` for every county or
tract of the state in `census_layer_value`. --report prints, from what is
stored, the share of places each variable is unreliable in: the measure
behind the registry's tract flags.
"""

from django.core.management.base import BaseCommand, CommandError

from visuals import census


class Command(BaseCommand):
    help = "Fetch ACS layers for counties or tracts into census_layer_value."

    def add_arguments(self, parser):
        parser.add_argument("--level", choices=census.LEVELS, required=True)
        parser.add_argument("--state", default="29")
        parser.add_argument("--year", type=int, default=census.YEAR)
        parser.add_argument(
            "--variables", default="", help="keys, comma-separated; default all"
        )
        parser.add_argument(
            "--report",
            action="store_true",
            help="reliability of what is stored; no fetch",
        )

    def handle(self, *args, **options):
        level, year = options["level"], options["year"]
        if options["report"]:
            for r in census.reliability(level, year):
                share = f"{r['share']:.0%}" if r["share"] is not None else "-"
                self.stdout.write(
                    f"{r['label']:40} {r['places']:>6} places  "
                    f"{r['hatched']:>6} unreliable  {share:>5}"
                )
            return
        keys = [k for k in options["variables"].split(",") if k]
        unknown = [k for k in keys if k not in census.BY_KEY]
        if unknown:
            raise CommandError(f"unknown variables: {', '.join(unknown)}")
        variables = [census.BY_KEY[k] for k in keys] or None
        try:
            rows = census.fetch(level, options["state"], year, variables)
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc
        counts = census.store(rows, level, year)
        self.stdout.write(
            f"census layers: {counts['rows']} values stored for {level} "
            f"{options['state']} ({year} ACS 5-year); "
            f"{counts['before']} were stored before"
        )
