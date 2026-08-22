"""Run an import batch from a file, for backpatches too large or too
scripted for the browser upload.

Same protocol as the UI (SCOPE.md §2.3): parse, map, diff, then write
only when asked, through the audited path.

    ./infra/manage.sh apply_import --gcs gs://bucket/sources.csv \
        --target sources --state MO
    ./infra/manage.sh apply_import --gcs gs://bucket/sources.csv \
        --target sources --state MO --apply --actor you@localnewsimpact.org

The key column is the target's UUID; a file without one is refused
rather than matched on something that merely looks like an identifier.
"""

import csv
import io

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from review.imports import TARGETS, compute_diff, guess_key_column, importable_fields
from review.models import ImportBatch
from review.services import audited_update_rows


class Command(BaseCommand):
    help = "Diff (and optionally apply) a CSV against articles or sources."

    def add_arguments(self, parser):
        parser.add_argument("--gcs", help="gs://bucket/object holding the CSV")
        parser.add_argument("--path", help="local CSV path (development)")
        parser.add_argument("--target", default="sources", choices=list(TARGETS))
        parser.add_argument(
            "--state",
            default="",
            help="two-letter state the gazetteer checks city and county against",
        )
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--actor", default="")
        parser.add_argument("--limit", type=int, default=40)

    def handle(self, *args, **options):
        text = self._read(options)
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            raise CommandError("No data rows.")
        columns = list(reader.fieldnames)
        target = options["target"]
        key_column = guess_key_column(columns, target)
        if not key_column:
            raise CommandError(
                f"No {TARGETS[target]['key_label']} column in {columns}. "
                "Export from Datadesk first — the export carries the id."
            )
        fields = importable_fields(target)
        column_map = {c: c for c in columns if c in fields}
        if not column_map:
            raise CommandError(f"No importable columns; expected any of {fields}")

        batch = ImportBatch(
            created_by=self._actor(options["actor"]),
            filename=options.get("gcs") or options.get("path") or "(stream)",
            target=target,
            columns=columns,
            rows=rows,
            key_column=key_column,
            column_map=column_map,
        )
        batch.validate_state = options["state"]
        diff = compute_diff(batch)

        self.stdout.write(
            f"{len(rows)} rows | key {key_column} | "
            + " ".join(f"{k}={v}" for k, v in diff["counts"].items())
        )
        shown = 0
        for row in diff["report"]:
            for field in row["fields"]:
                if shown >= options["limit"]:
                    break
                mark = "BLOCKED" if field["kind"] == "suspect" else field["kind"]
                self.stdout.write(
                    f"  {mark} {row['id'][:8]} {field['field']}: "
                    f"{field['current']!r} -> {field['incoming']!r}"
                    + (f" ({field['reason']})" if field.get("reason") else "")
                )
                shown += 1

        if not options["apply"]:
            self.stdout.write("Nothing written. Re-run with --apply.")
            return
        if not diff["changes"]:
            self.stdout.write("Nothing to apply.")
            return

        batch.save()
        entry = audited_update_rows(
            batch.created_by,
            TARGETS[target]["model"],
            diff["changes"],
            action="import:apply",
            reason=f"import batch {batch.pk}: {batch.filename}",
        )
        batch.status = ImportBatch.APPLIED
        batch.applied_at = timezone.now()
        batch.audit_entry = entry
        batch.save(update_fields=["status", "applied_at", "audit_entry"])
        self.stdout.write(
            f"Applied {len(diff['changes'])} rows as batch {batch.pk}; "
            f"audit entry {entry.pk} reverts it."
        )

    def _read(self, options):
        if options.get("gcs"):
            from google.cloud import storage

            path = options["gcs"]
            if not path.startswith("gs://"):
                raise CommandError("--gcs must look like gs://bucket/object")
            bucket, _, blob = path[5:].partition("/")
            data = storage.Client().bucket(bucket).blob(blob).download_as_bytes()
            return data.decode("utf-8-sig")
        if options.get("path"):
            with open(options["path"], encoding="utf-8-sig") as fh:
                return fh.read()
        raise CommandError("Pass --gcs or --path.")

    def _actor(self, email):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = (
            User.objects.filter(email__iexact=email).first()
            if email
            else User.objects.filter(is_superuser=True).order_by("id").first()
        )
        if user is None:
            raise CommandError("No actor for the writes; pass --actor <email>.")
        return user
