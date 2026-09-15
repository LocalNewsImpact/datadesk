"""Fetch a state's block-to-place crosswalk and store it.

A block GEOID carries state, county, tract and block group and never the
city, so counting a block-coded story under its city needs a table the
Census publishes per state.

    python manage.py load_block_places MO
    python manage.py load_block_places --all-loaded     # what is here

Missouri is 253,633 blocks from a 4.3 MB download, of which 119,139 are
in a place and 134,493 -- 53% -- are unincorporated and in none. Both are
stored: "looked, and there is no city" is an answer, and it must not send
the next caller back to census.gov.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from datasets import blockplace
from datasets.geo import state_code
from datasets.models import BlockPlaceLoad


class Command(BaseCommand):
    help = "Fetch and store the Census block-to-place crosswalk for a state."

    def add_arguments(self, parser):
        parser.add_argument(
            "state",
            nargs="?",
            help="State as USPS (MO) or name (Missouri).",
        )
        parser.add_argument(
            "--all-loaded",
            action="store_true",
            help="Report which states are already stored, and stop.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Re-fetch a state already stored. Its rows are replaced, "
                "not merged -- a half-old assignment would put one story "
                "in two cities."
            ),
        )

    def handle(self, *args, **options):
        if options["all_loaded"]:
            rows = BlockPlaceLoad.objects.all()
            if not rows:
                self.stdout.write("No state crosswalks stored yet.")
                return
            self.stdout.write(f"{'state':<7}{'blocks':>10}{'in a place':>12}  loaded")
            for row in rows:
                self.stdout.write(
                    f"{row.usps:<7}{row.blocks:>10}{row.in_a_place:>12}  "
                    f"{row.loaded_at:%Y-%m-%d}"
                )
            return

        if not options["state"]:
            raise CommandError("Name a state, or pass --all-loaded.")

        usps = state_code(options["state"])
        if not usps:
            raise CommandError(f"{options['state']!r} is not a state.")
        fips = _fips_for(usps)
        if not fips:
            raise CommandError(f"No FIPS for {usps}.")

        if options["force"]:
            BlockPlaceLoad.objects.filter(state_fips=fips).delete()
        elif blockplace.is_loaded(fips):
            self.stdout.write(f"{usps} is already stored. Use --force to refetch.")
            return

        self.stdout.write(f"Fetching {usps} from {blockplace.url_for(fips, usps)} ...")
        load = blockplace.load_state(fips, usps)
        share = (load.in_a_place / load.blocks * 100) if load.blocks else 0
        self.stdout.write(
            self.style.SUCCESS(
                f"{usps}: {load.blocks} blocks, {load.in_a_place} in a place "
                f"({share:.0f}%), the rest unincorporated."
            )
        )


def _fips_for(usps: str) -> str:
    """State FIPS for a USPS code, from the county gazetteer already here.

    The first two digits of any county GEOID are its state, so this needs
    no extra file.
    """
    import csv
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "data" / "census_counties.csv"
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            if row["USPS"].upper() == usps.upper():
                return row["GEOID"][:2]
    return ""
