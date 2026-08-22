"""Turn a reconciliation CSV into proposals for the review queue.

The CSV is the findings export: one row per field that differs, with
what the checks concluded. This command stores them for a human to
decide; it writes nothing to the corpus.

    ./infra/manage.sh load_source_proposals \
        --gcs gs://bucket/mizzou_source_findings.csv --origin "Sources sheet"
"""

import csv
import io

from django.core.management.base import BaseCommand, CommandError

from review.proposals import ChangeProposal

# The findings vocabulary the export writes, mapped to the queue's own.
FINDING_MAP = {
    "apply": ChangeProposal.READY,
    "blocked: ownership change": ChangeProposal.OWNER_CONFLICT,
    "blocked: unknown owner": ChangeProposal.UNKNOWN_OWNER,
    "blocked: gazetteer": ChangeProposal.GAZETTEER,
    "duplicate row": ChangeProposal.DUPLICATE,
    "no matching source": ChangeProposal.NO_MATCH,
    "excluded": ChangeProposal.OWNER_CONFLICT,
    "blank ignored": ChangeProposal.READY,
}


class Command(BaseCommand):
    help = "Load reconciliation findings into the proposal queue."

    def add_arguments(self, parser):
        parser.add_argument("--gcs")
        parser.add_argument("--path")
        parser.add_argument("--origin", default="imported sheet")
        parser.add_argument("--dataset", default="")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="clear pending proposals from this origin first",
        )

    def handle(self, *args, **options):
        text = self._read(options)
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise CommandError("No rows.")

        origin = options["origin"]
        if options["replace"]:
            removed, _ = ChangeProposal.objects.filter(
                origin=origin, state=ChangeProposal.PENDING
            ).delete()
            self.stdout.write(f"cleared {removed} pending from {origin!r}")

        made, skipped = 0, 0
        for row in rows:
            record_id = (row.get("source_id") or "").strip()
            field = (row.get("field") or "").strip()
            if not record_id or field in ("", "(row)"):
                # A row with no record is a problem with the file, not a
                # change anyone can decide on.
                skipped += 1
                continue
            ChangeProposal.objects.create(
                target="sources",
                record_id=record_id,
                record_label=(row.get("host_norm") or "").strip(),
                dataset=options["dataset"],
                origin=origin,
                field=field,
                current_value=(row.get("current") or "").strip(),
                proposed_value=(row.get("proposed") or "").strip(),
                finding=FINDING_MAP.get(
                    (row.get("finding") or "").strip(), ChangeProposal.READY
                ),
                why=(row.get("why") or "").strip(),
                suggestion=(row.get("suggestion") or "").strip(),
            )
            made += 1

        self.stdout.write(f"loaded {made} proposals; skipped {skipped} rows")
        for key, label in ChangeProposal.FINDINGS:
            n = ChangeProposal.objects.filter(
                origin=origin, state=ChangeProposal.PENDING, finding=key
            ).count()
            if n:
                self.stdout.write(f"  {label}: {n}")

    def _read(self, options):
        if options.get("gcs"):
            from google.cloud import storage

            path = options["gcs"]
            bucket, _, blob = path[5:].partition("/")
            data = storage.Client().bucket(bucket).blob(blob).download_as_bytes()
            return data.decode("utf-8-sig")
        if options.get("path"):
            with open(options["path"], encoding="utf-8-sig") as fh:
                return fh.read()
        raise CommandError("Pass --gcs or --path.")
