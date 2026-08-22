"""Normalize publisher county names against the Census gazetteer.

Reports first, applies only when asked — the same diff-then-apply
discipline as the CSV import (SCOPE.md §2.4), and every write goes
through the audited path so the change is attributable and revertible.

    ./infra/manage.sh normalize_counties --dataset Mizzou-Missouri-State
    ./infra/manage.sh normalize_counties --dataset Mizzou-Missouri-State --apply

Three outcomes per source:

  clean      the value already matches the gazetteer's spelling
  rewrite    it resolves to exactly one county under a different
             spelling ("St Louis" -> "St. Louis", "SAINTE GENEVIEVE
             COUNTY" -> "Ste. Genevieve") and can be rewritten safely
  review     it names more than one county ("Jasper and Newton") or
             matches nothing; a human decides, never this command
"""

import re

from django.core.management.base import BaseCommand, CommandError

from datasets.geo import (
    canonical_county,
    place_county,
    state_code,
    states_with_county,
    suggest_counties,
)
from explorer.models import Dataset, DatasetSource, Source
from review.services import audited_update

SPLIT = re.compile(r"\s*(?:,|/|;|&|\band\b)\s*", re.IGNORECASE)


def classify(state, value):
    """(kind, canonical, detail) for one county value."""
    raw = (value or "").strip()
    if not raw:
        return "review", None, "no county recorded"

    fips, canonical = canonical_county(state, raw)
    if canonical:
        return ("clean" if canonical == raw else "rewrite"), canonical, fips

    parts = [p for p in SPLIT.split(raw) if p.strip()]
    if len(parts) > 1:
        named = [canonical_county(state, p)[1] for p in parts]
        if all(named):
            return (
                "review",
                None,
                "names several counties: " + ", ".join(named),
            )
        return "review", None, f"names several places: {raw}"

    # A value that matches nothing here often matches something obvious
    # elsewhere: a county in the neighbouring state, or a city rather
    # than a county. Naming that turns a dead end into a correction.
    elsewhere = [st for st in states_with_county(raw) if st != state_code(state)]
    if elsewhere:
        return (
            "review",
            None,
            f"{raw} is a county in {', '.join(elsewhere)}, not "
            f"{state_code(state)} — check the state or the county",
        )
    as_place = place_county(state, raw)
    if as_place:
        return (
            "review",
            None,
            f"{raw} is a city, not a county; it sits in {as_place[2]}",
        )
    hints = suggest_counties(state, raw)
    return (
        "review",
        None,
        (
            "no gazetteer match"
            + (f"; did you mean {', '.join(hints)}?" if hints else "")
        ),
    )


class Command(BaseCommand):
    help = "Report (and optionally apply) publisher county normalizations."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", required=True, help="dataset slug")
        parser.add_argument(
            "--state",
            default="",
            help="two-letter state for matching; defaults to the dataset's "
            "default_state, then to each source's own metadata state",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="write the safe rewrites through the audited path",
        )
        parser.add_argument(
            "--actor",
            default="",
            help="email of the user to record as the actor when applying",
        )

    def handle(self, *args, **options):
        dataset = Dataset.objects.filter(slug=options["dataset"]).first()
        if dataset is None:
            raise CommandError(f"No dataset with slug {options['dataset']}")
        default_state = (
            options["state"] or (dataset.meta or {}).get("default_state") or ""
        ).upper()

        source_ids = DatasetSource.objects.filter(dataset_id=dataset.id).values_list(
            "source_id", flat=True
        )
        sources = list(
            Source.objects.filter(id__in=list(source_ids)).order_by("county")
        )

        buckets = {"clean": [], "rewrite": [], "review": []}
        for source in sources:
            state = ((source.meta or {}).get("state") or default_state or "").upper()
            kind, canonical, detail = classify(state, source.county)
            buckets[kind].append((source, canonical, detail, state))

        self.stdout.write(
            f"{dataset.label}: {len(sources)} sources · "
            f"{len(buckets['clean'])} clean · "
            f"{len(buckets['rewrite'])} to rewrite · "
            f"{len(buckets['review'])} need review"
        )
        for source, canonical, _detail, _state in buckets["rewrite"]:
            self.stdout.write(
                f"  rewrite  {source.host_norm}: " f"{source.county!r} -> {canonical!r}"
            )
        for source, _canonical, detail, _state in buckets["review"]:
            self.stdout.write(
                f"  review   {source.host_norm}: {source.county!r} — {detail}"
            )

        if not options["apply"]:
            self.stdout.write("Nothing written. Re-run with --apply to rewrite.")
            return
        if not buckets["rewrite"]:
            self.stdout.write("Nothing to rewrite.")
            return

        actor = self._actor(options["actor"])
        # One audit entry per target spelling, so a revert restores the
        # exact previous values for that group.
        by_value = {}
        for source, canonical, _d, _s in buckets["rewrite"]:
            by_value.setdefault(canonical, []).append(source)
        for canonical, group in by_value.items():
            audited_update(
                actor,
                group,
                {"county": canonical},
                action="source:normalize_county",
                reason=f"Census gazetteer spelling for {dataset.label}",
            )
            self.stdout.write(f"  applied  {len(group)} -> {canonical!r}")
        self.stdout.write(f"Applied {len(buckets['rewrite'])} rewrites.")

    def _actor(self, email):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = (
            User.objects.filter(email__iexact=email).first()
            if email
            else User.objects.filter(is_superuser=True).order_by("id").first()
        )
        if user is None:
            raise CommandError(
                "No actor to attribute the writes to; pass --actor <email>."
            )
        return user
