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


def _settle(value):
    """Compare field values the way a person reads them."""
    return " ".join(str(value).split()).casefold()


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
            "default_state": default_state,
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
        settled = self._settled()
        pending_rows = {
            (row.record_id, row.flag, row.field): row
            for row in ChangeProposal.objects.filter(
                target="sources", state=ChangeProposal.PENDING
            )
        }
        existing = set(pending_rows)

        retired = self._retire_settled(sources, settled, existing, options)

        made = collections.Counter()
        refreshed = 0
        live_keys = set()
        for source in sources:
            for item in self._flags_for(source, context, evidence, options):
                key = (source.id, item["flag"], item["field"])
                live_keys.add(key)
                if key in existing:
                    refreshed += self._refresh(pending_rows[key], item, options)
                    continue
                live = _settle(getattr(source, item["field"], "") or "")
                ruled = settled.get((source.id, item["field"]), {})
                # Settled while the field still reads as it did when the
                # decision was made; a later change re-opens the question.
                if live in ruled.get(item["flag"], ()) or live in ruled.get("", ()):
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
        if refreshed:
            self.stdout.write(
                f"{'would answer' if options['dry_run'] else 'answered'} "
                f"{refreshed} that had nothing to propose"
            )
        if retired:
            self.stdout.write(
                f"{'would clear' if options['dry_run'] else 'cleared'} "
                f"{retired} already answered"
            )
        # A check that has been corrected leaves behind the proposals it
        # wrongly raised, and nothing was clearing them: _retire_settled
        # sweeps questions a person has answered, not questions the app
        # has stopped asking.
        withdrawn = self._withdraw_unraised(sources, live_keys, options)
        if withdrawn:
            self.stdout.write(
                f"{'would withdraw' if options['dry_run'] else 'withdrew'} "
                f"{withdrawn} no longer flagged"
            )
        self.stdout.write(
            f"{len(sources)} publishers scanned; "
            f"{'would queue' if options['dry_run'] else 'queued'} {total}"
        )
        # A check that has been corrected leaves behind the proposals it
        # wrongly raised, and nothing was clearing them: `_retire_settled`
        # sweeps questions a person has answered, not questions the app has
        # stopped asking. The misfiled-column check flagged every name that
        # is both a city and a county -- St. Louis, Jackson, Jasper -- and
        # fixing it left ninety-odd of them in the queue for good.
        for flag, count in made.most_common():
            self.stdout.write(f"  {flag}: {count}")

    def _withdraw_unraised(self, sources, live_keys, options):
        """Delete pending rows for scanned sources that no longer flag.

        Either the record was corrected or the check was. Both mean the
        question is not being asked any more, and a queue holding questions
        nothing asks is a queue nobody trusts.

        Only sources this run actually scanned: a proposal on a record
        outside the dataset was not re-examined and must not be swept on
        the strength of not having been looked at.
        """
        scanned = {source.id for source in sources}
        pending = ChangeProposal.objects.filter(
            target="sources",
            state=ChangeProposal.PENDING,
            record_id__in=scanned,
        )
        stale = [
            row.pk
            for row in pending
            if (row.record_id, row.flag, row.field) not in live_keys
        ]
        if stale and not options["dry_run"]:
            ChangeProposal.objects.filter(pk__in=stale).delete()
        return len(stale)

    def _retire_settled(self, sources, settled, existing, options):
        """Clear questions still in the queue that have already been answered.

        A queue that re-asks a settled question wastes the reviewer's
        time whether the row was made before or after the decision, so
        the scan sweeps as well as adds.
        """
        stale = []
        by_id = {source.id: source for source in sources}
        pending = ChangeProposal.objects.filter(
            target="sources", state=ChangeProposal.PENDING
        )
        for row in pending:
            source = by_id.get(row.record_id)
            if source is None:
                continue
            ruled = settled.get((row.record_id, row.field))
            if not ruled:
                continue
            live = _settle(getattr(source, row.field, "") or "")
            if live in ruled.get(row.flag, ()) or live in ruled.get("", ()):
                stale.append(row.pk)
                existing.discard((row.record_id, row.flag, row.field))
        if stale and not options["dry_run"]:
            ChangeProposal.objects.filter(pk__in=stale).delete()
        return len(stale)

    def _settled(self):
        """What has already been ruled on, as (record, field) -> flag -> values.

        A disposition is a ruling on one field *at the value it then held*
        (REVIEW.md §4): keeping a value settles that value, applying one
        settles what was written. The rescan re-asks only when the field
        no longer reads that way. Decisions taken before the flag
        vocabulary existed carry an empty flag and settle the field
        against every flag — the person ruled on the value, whatever we
        called the defect at the time.
        """
        settled = collections.defaultdict(lambda: collections.defaultdict(set))
        rows = ChangeProposal.objects.filter(target="sources").exclude(
            state=ChangeProposal.PENDING
        )
        for row in rows.values_list(
            "record_id",
            "field",
            "flag",
            "state",
            "current_value",
            "proposed_value",
            "final_value",
        ):
            record_id, field, flag, state, current, proposed, final = row
            # Rejected keeps the value as it was; anything else applied
            # a value over it.
            rejected = state == ChangeProposal.REJECTED
            value = current if rejected else (final or proposed)
            settled[(record_id, field)][flag].add(_settle(value or ""))
        return settled

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
            # A dotted field is a key inside a JSON column. Splitting on the
            # dot and keeping the first half looked for an attribute called
            # `meta` and reported "" for every state defect, so the reviewer
            # was shown a blank where the wrong value should have been.
            field, _, inner = flag.field.partition(".")
            holder = getattr(source, field, None)
            if inner:
                current = (holder or {}).get(inner) or "" if holder is not None else ""
            else:
                current = (holder or "") if hasattr(source, field) else ""
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
                    # The whole path, because this is what the accept step
                    # writes back. Storing the column alone raised the
                    # proposal and then refused to apply it.
                    "field": flag.field,
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
                    "detail": self._evidence_detail(candidate, current),
                    "current_value": current,
                    "proposed_value": value,
                    "suggestion": "",
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

    def _refresh(self, row, item, options):
        """Give a queued question the answer evidence has since supplied.

        A flag can be raised before anything is known to propose — "no
        owner recorded" with no candidate anywhere is a research task,
        not a decision. When a later reading of a directory offers a
        value, the question already in the queue should carry it rather
        than staying blank while a duplicate is refused as a duplicate.

        Only an empty proposal is filled. A row a reviewer is already
        looking at does not have its proposed value changed underneath
        them.
        """
        offered = (item.get("proposed_value") or "").strip()
        if not offered or (row.proposed_value or "").strip():
            return 0
        if not options["dry_run"]:
            row.proposed_value = offered
            row.detail = item.get("detail", row.detail)
            row.suggestion = item.get("suggestion", row.suggestion)
            row.origin = item.get("origin", row.origin)
            row.save(update_fields=["proposed_value", "detail", "suggestion", "origin"])
        return 1

    @staticmethod
    def _evidence_detail(candidate, current):
        """What the file said, in one sentence a person can act on."""
        origin = candidate["origin"]
        held = (
            f"the record says \u201c{current}\u201d"
            if current
            else ("the record has no value")
        )
        found = candidate.get("candidates") or [candidate.get("value", "")]
        if len(found) > 1:
            listed = " and ".join(f"\u201c{v}\u201d" for v in found)
            return (
                f"{origin} gives more than one value here — {listed} — "
                f"so neither is proposed; {held}"
            )
        return f"{origin} says \u201c{found[0]}\u201d; {held}"

    def _evidence(self, options):
        """Candidate values from a file, keyed by (host, field).

        Every distinct value is kept, not the last row's. Two rows for
        one host is ordinary — a section of a paper shares its parent's
        domain — and says nothing about whether the file disagrees with
        itself. Only two different values for the *same field* do, and
        even then the reviewer can decide: the candidates are named and
        neither is proposed.
        """
        if not options["evidence"]:
            return {}
        text = self._read(options["evidence"])
        rows = list(csv.DictReader(io.StringIO(text)))
        name = options["evidence_name"]

        values = collections.defaultdict(list)
        for row in rows:
            host = (row.get("host_norm") or "").strip().lower()
            if not host:
                continue
            for field in EVIDENCE_FIELDS:
                value = (row.get(field) or "").strip()
                if value and value not in values[(host, field)]:
                    values[(host, field)].append(value)

        return {
            key: {
                "value": found[0] if len(found) == 1 else "",
                "candidates": found,
                "origin": name,
                "conflicting": len(found) > 1,
                "note": f"from {name}",
            }
            for key, found in values.items()
        }

    def _read(self, path):
        if path.startswith("gs://"):
            from google.cloud import storage

            bucket, _, blob = path[5:].partition("/")
            return (
                storage.Client().bucket(bucket).blob(blob).download_as_bytes()
            ).decode("utf-8-sig")
        with open(path, encoding="utf-8-sig") as fh:
            return fh.read()
