"""Scan publisher records for defects and queue what is wrong (REVIEW.md).

The scan reads the corpus, not an import: every record that might be
incorrect or incomplete surfaces, whether or not any file mentioned it.
An imported file is evidence — it can supply a candidate value for a
flagged field, or raise a flag by disagreeing with what is recorded —
but it never decides what belongs in the queue.

    ./infra/manage.sh scan_sources --dataset Mizzou-Missouri-State
    ./infra/manage.sh scan_sources --dataset Mizzou-Missouri-State \
        --evidence gs://bucket/sources.csv --evidence-name "Sources sheet"
"""

import collections
import csv
import io

from django.core.management.base import BaseCommand, CommandError

from datasets.geo import state_code
from explorer.models import Dataset, DatasetSource, Source
from review.flags import FLAGS
from review.proposals import ChangeProposal

EVIDENCE_FIELDS = ("canonical_name", "city", "county", "owner", "type")


class Command(BaseCommand):
    help = "Flag publisher records that are incorrect or incomplete."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True)
        parser.add_argument("--state", default="")
        parser.add_argument("--evidence", default="", help="gs:// or local CSV")
        parser.add_argument("--evidence-name", default="a source file")
        parser.add_argument(
            "--dry-run", action="store_true", help="report without queueing"
        )

    def handle(self, *args, **options):
        dataset = Dataset.objects.filter(slug=options["dataset"]).first()
        if dataset is None:
            raise CommandError(f"No dataset {options['dataset']}")
        default_state = (
            options["state"] or (dataset.meta or {}).get("default_state") or ""
        )

        ids = list(
            DatasetSource.objects.filter(dataset_id=dataset.id).values_list(
                "source_id", flat=True
            )
        )
        sources = list(Source.objects.filter(id__in=ids))
        context = {
            "state_of": lambda s: state_code(
                (s.meta or {}).get("state") or default_state
            ),
            "owners": {
                owner
                for owner in Source.objects.exclude(owner="").values_list(
                    "owner", flat=True
                )
                if owner
            },
        }
        evidence = self._evidence(options)

        # A decision already made is not asked again (REVIEW.md §4).
        decided = set(
            ChangeProposal.objects.filter(target="sources")
            .exclude(state=ChangeProposal.PENDING)
            .values_list("record_id", "flag", "field")
        )
        existing = set(
            ChangeProposal.objects.filter(
                target="sources", state=ChangeProposal.PENDING
            ).values_list("record_id", "flag", "field")
        )

        made = collections.Counter()
        for source in sources:
            for item in self._flags_for(source, context, evidence, options):
                key = (source.id, item["flag"], item["field"])
                if key in decided or key in existing:
                    continue
                made[item["flag"]] += 1
                if not options["dry_run"]:
                    ChangeProposal.objects.create(
                        target="sources",
                        record_id=source.id,
                        record_label=source.host_norm,
                        dataset=dataset.slug,
                        origin=item.pop("origin", "corpus scan"),
                        **item,
                    )
                    existing.add(key)

        total = sum(made.values())
        self.stdout.write(
            f"{len(sources)} publishers scanned; "
            f"{'would queue' if options['dry_run'] else 'queued'} {total}"
        )
        for flag, count in made.most_common():
            self.stdout.write(f"  {flag}: {count}")

    def _flags_for(self, source, context, evidence, options):
        """Every defect on one record, as queue items.

        The proposed value is always the one we believe correct: what a
        check says it should be, or a candidate from a file that passes
        the same checks. A candidate that fails them is not proposed —
        offering it under "Accept" would ask the reviewer to write a
        value the app has just called wrong.
        """
        items = []
        for flag in FLAGS:
            flagged, detail, better = flag.check(source, context)
            if not flagged:
                continue
            field = flag.field.split(".")[0]
            current = (
                (getattr(source, field, "") or "") if hasattr(source, field) else ""
            )
            candidate = evidence.get((source.host_norm, field), {})
            proposed = better
            origin = "corpus scan"
            if not proposed and candidate:
                value = candidate.get("value", "").strip()
                if (
                    value
                    and value != current
                    and self._acceptable(field, value, source, context)
                ):
                    proposed, origin = value, candidate["origin"]
            items.append(
                {
                    "flag": flag.key,
                    "field": field,
                    "detail": detail,
                    "current_value": current,
                    "proposed_value": proposed,
                    "suggestion": (
                        f"suggested by {origin}"
                        if proposed and origin != "corpus scan"
                        else ""
                    ),
                    "origin": origin,
                }
            )

        # Evidence that disagrees with a record the checks are happy with
        # is worth a look — but only when the file's value is itself
        # sound. A file proposing a misspelling against a correct record
        # is the file's problem, not a change to offer.
        flagged_fields = {item["field"] for item in items}
        for field in EVIDENCE_FIELDS:
            candidate = evidence.get((source.host_norm, field))
            if not candidate or field in flagged_fields:
                continue
            current = (getattr(source, field, "") or "").strip()
            value = candidate.get("value", "").strip()
            if not value or value == current:
                continue
            if not self._acceptable(field, value, source, context):
                continue
            items.append(
                {
                    "flag": (
                        "evidence_conflict"
                        if candidate.get("conflicting")
                        else "value_disputed"
                    ),
                    "field": field,
                    "detail": (
                        f"{candidate['origin']} says {value}; the record says "
                        f"{current or 'nothing'}"
                    ),
                    "current_value": current,
                    "proposed_value": value,
                    "suggestion": f"from {candidate['origin']}",
                    "origin": candidate["origin"],
                }
            )
        return items

    def _acceptable(self, field, value, source, context):
        """Would this value pass the checks that guard the field?"""
        state = context["state_of"](source)
        if field == "county":
            from datasets.geo import canonical_county

            return bool(canonical_county(state, value)[1]) if state else True
        if field == "city":
            from datasets.places import validate_city

            return validate_city(state, value)[0] if state else True
        if field == "owner":
            from datasets.owners import canonical_owner

            return canonical_owner(value, context["owners"])[1] != "unknown"
        return True

    def _evidence(self, options):
        """Candidate values from a file, keyed by (host, field)."""
        if not options["evidence"]:
            return {}
        text = self._read(options["evidence"])
        rows = list(csv.DictReader(io.StringIO(text)))
        name = options["evidence_name"]
        seen = collections.Counter(
            (r.get("host_norm") or "").strip().lower() for r in rows
        )
        out = {}
        for row in rows:
            host = (row.get("host_norm") or "").strip().lower()
            if not host:
                continue
            for field in EVIDENCE_FIELDS:
                value = (row.get(field) or "").strip()
                if not value:
                    continue
                out[(host, field)] = {
                    "value": value,
                    "origin": name,
                    "conflicting": seen[host] > 1,
                    "note": f"from {name}",
                }
        return out

    def _read(self, path):
        if path.startswith("gs://"):
            from google.cloud import storage

            bucket, _, blob = path[5:].partition("/")
            return (
                storage.Client().bucket(bucket).blob(blob).download_as_bytes()
            ).decode("utf-8-sig")
        with open(path, encoding="utf-8-sig") as fh:
            return fh.read()
