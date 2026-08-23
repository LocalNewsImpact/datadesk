"""Match the Missouri Press directory to our publishers and report the gaps.

Writes an evidence CSV for `scan_sources --evidence`, which is what
turns a disagreement into a question in the review queue. Writes
nothing to the corpus itself.

The report is the other half and the part a person reads: which
directory entries found no publisher record (candidates the corpus may
be missing — this never creates them), which were too ambiguous to
pair, and what the matched pairs disagree about.
"""

import collections
import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from explorer.models import Dataset, DatasetSource, Source
from review import mopress

COMPARED = ("city", "county", "owner")


class Command(BaseCommand):
    help = "Compare the Missouri Press directory with our publisher records."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument(
            "--document",
            default="",
            help="The reading to compare. Defaults to the newest mopress-*.json.",
        )
        parser.add_argument(
            "--out", default="", help="Where to write the evidence CSV."
        )
        parser.add_argument(
            "--gaps",
            default="",
            help="Where to write the unmatched-entry report as CSV.",
        )

    def handle(self, **options):
        dataset = Dataset.objects.filter(slug=options["dataset"]).first()
        if dataset is None:
            raise CommandError(f"No dataset {options['dataset']}")

        path = options["document"]
        if not path:
            folder = Path(settings.BASE_DIR) / "data" / "sources"
            readings = sorted(folder.glob("mopress-*.json"))
            if not readings:
                raise CommandError("No mopress reading in data/sources")
            path = readings[-1]
        document, records = mopress.load(path)
        self.stdout.write(
            f"{document['source']}, read {document['fetched']}: "
            f"{len(records)} publications"
        )

        ids = list(
            DatasetSource.objects.filter(dataset_id=dataset.id).values_list(
                "source_id", flat=True
            )
        )
        sources = list(Source.objects.filter(id__in=ids))
        self.stdout.write(f"{len(sources)} publisher records in {dataset.slug}")

        matched, unmatched, ambiguous, used = mopress.match(records, sources)
        by_basis = collections.Counter(basis for _, _, basis in matched)
        self.stdout.write(
            f"matched {len(matched)} "
            f"(by website {by_basis['website']}, by name {by_basis['name']})"
        )
        self.stdout.write(f"no publisher record: {len(unmatched)}")
        self.stdout.write(f"too ambiguous to pair: {len(ambiguous)}")
        self.stdout.write(
            f"publishers the directory does not list: {len(sources) - len(used)}"
        )

        rows = mopress.evidence_rows(matched)
        fills, conflicts = self._classify(matched, rows)
        self.stdout.write("")
        self.stdout.write("would fill an empty field:")
        for field, count in sorted(fills.items()):
            self.stdout.write(f"  {field}: {count}")
        self.stdout.write("disagrees with what we hold:")
        for field, count in sorted(conflicts.items()):
            self.stdout.write(f"  {field}: {count}")

        no_owner_name = sum(
            1 for record, _, _ in matched if not mopress.usable_owner(record["owner"])
        )
        self.stdout.write(
            f"\nownership given as a category, not a company: {no_owner_name} "
            "(reported, never proposed)"
        )

        out = Path(options["out"] or "/tmp/mopress-evidence.csv")
        columns = ["host_norm", "canonical_name", "city", "county", "owner", "type"]
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        self.stdout.write(f"\nevidence: {out}")

        gaps = Path(options["gaps"] or "/tmp/mopress-gaps.csv")
        with gaps.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["status", "name", "website", "city", "county", "owner", "circulation"]
            )
            for status, group in (("no record", unmatched), ("ambiguous", ambiguous)):
                for record in group:
                    writer.writerow(
                        [
                            status,
                            record.get("name", ""),
                            record.get("website", ""),
                            record.get("city", ""),
                            mopress.fold_county(record.get("county")),
                            record.get("owner", ""),
                            record.get("circulation", ""),
                        ]
                    )
        self.stdout.write(f"gaps: {gaps}")

        self.stdout.write("\nDirectory entries with no publisher record:")
        for record in unmatched:
            city = record.get("city") or "?"
            self.stdout.write(
                f"  {record.get('name', '')[:44]:46} {city[:18]:20} "
                f"{record.get('website', '')[:40]}"
            )

    def _classify(self, matched, rows):
        fills = collections.Counter()
        conflicts = collections.Counter()
        for (_, source, _), row in zip(matched, rows, strict=True):
            for field in COMPARED:
                offered = (row.get(field) or "").strip()
                if not offered:
                    continue
                held = (getattr(source, field, "") or "").strip()
                if not held:
                    fills[field] += 1
                elif held.casefold() != offered.casefold():
                    conflicts[field] += 1
        return fills, conflicts
