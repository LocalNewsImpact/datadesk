"""Draw a cohort of articles for the classification queue, and assign it.

    python manage.py draw_classification_cohort --size 300 --coders ed,sam,jo

Reviewable as a table before any UI exists, which is while the sampling
can still be argued with.

WHAT IT DRAWS
Four strata, each answering a different question (see
docs/CLASSIFICATION_REVIEW_QUEUE.md section 3):

    confident   the model scored >= 0.7 -- does a human agree where it
                is sure
    uncertain   scored < 0.5 -- does it agree where it is guessing
    random      drawn uniformly, the only unbiased estimate of anything
    unlabelled  the analysis stage has not reached it, so there is
                nothing to agree with and the label is training data
                outright

Confident and uncertain are drawn in equal size on purpose. A model that
is right when sure and wrong when guessing is usable with a threshold;
one equally wrong in both is a different problem, and an overall
accuracy figure cannot tell them apart.

WHY IT OVERSAMPLES THE RARE CATEGORIES
The corpus runs 13:1 from Civic Life to Transportation Systems, so a
uniform draw says almost nothing about the rare ones -- which is where a
model trained on the same distribution is likely worst. The original
labelling oversampled them for exactly this reason.

Oversampling costs nothing provided the inclusion probability is
recorded, which it is. Training wants the rare classes over-represented;
evaluation wants corpus rates; one draw serves both only because every
row says how likely it was to be here.

ASSIGNMENT
Every record gets `target_coders` assignments, spread evenly, so each
coder gets roughly the same amount of work and no coder sees a record
twice.
"""

from __future__ import annotations

import itertools
from collections import defaultdict

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from review.classification import (
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationSample,
)

#: Where the model is sure, and where it is guessing. The band between
#: them is deliberately not drawn: it is neither claim, and including it
#: blurs the one thing these two strata exist to separate. Rows there are
#: still reachable through the random stratum.
CONFIDENT_AT = 0.7
UNCERTAIN_BELOW = 0.5

#: How the size is split. Confident and uncertain match by design; the
#: random stratum is the only unbiased one and is not a leftover.
SHARES = {
    ClassificationSample.CONFIDENT: 0.3,
    ClassificationSample.UNCERTAIN: 0.3,
    ClassificationSample.RANDOM: 0.3,
    ClassificationSample.UNLABELLED: 0.1,
}


class Command(BaseCommand):
    help = "Draw a cohort for the classification queue and assign it"

    def add_arguments(self, parser):
        parser.add_argument("--size", type=int, default=300)
        parser.add_argument(
            "--coders",
            default="",
            help="Comma-separated usernames. Every record goes to "
            "`--target` of them, spread evenly.",
        )
        parser.add_argument("--target", type=int, default=3)
        parser.add_argument("--note", default="")
        parser.add_argument(
            "--balance-labels",
            action="store_true",
            help="Draw the confident and uncertain strata evenly across "
            "predicted labels rather than at corpus rates. Oversamples "
            "the rare categories, which is what makes them learnable; "
            "the inclusion probability records how much.",
        )
        parser.add_argument("--dry-run", action="store_true")

    # ---------------------------------------------------------------- draw

    def _labelled(self, low, high, balance, wanted):
        """Articles whose model confidence falls in [low, high).

        Drawn from `articles`, which carries the current label directly
        -- `primary_label` and its confidence are columns there, on
        123,890 of 164,940 rows. `article_labels` holds the history; this
        wants what the model currently says.
        """
        from explorer.models import Article

        rows = (
            Article.objects.using("crawler")
            .filter(
                primary_label__isnull=False,
                primary_label_confidence__gte=low,
                primary_label_confidence__lt=high,
            )
            .values("id", "primary_label", "primary_label_confidence")
        )
        # Ordered by a hash of the id rather than at random, so a rerun
        # draws the same rows and a cohort is reproducible.
        rows = rows.extra(select={"_d": "md5(articles.id)"}).order_by("_d")

        if not balance:
            picked = [dict(r, article_id=r["id"]) for r in rows[:wanted]]
            return picked, {r["article_id"]: 1.0 for r in picked}

        # Evenly across labels: take in turn from each until the quota is
        # filled, so a rare category is not crowded out by a common one.
        by_label: dict[str, list] = defaultdict(list)
        for row in rows[: max(wanted * 20, 2000)]:
            by_label[row["primary_label"]].append(dict(row, article_id=row["id"]))

        picked, probabilities = [], {}
        wheels = [iter(v) for v in by_label.values()]
        sizes = {label: len(v) for label, v in by_label.items()}
        for row in itertools.chain.from_iterable(itertools.zip_longest(*wheels)):
            if row is None:
                continue
            picked.append(row)
            # How over-represented this row is: its share of the draw
            # against its share of the pool. This is the number that lets
            # an oversampled draw be weighted back to corpus rates.
            label = row["primary_label"]
            probabilities[row["article_id"]] = (wanted / max(1, len(by_label))) / max(
                1, sizes[label]
            )
            if len(picked) >= wanted:
                break
        return picked, probabilities

    def _random(self, wanted, exclude):
        """Uniformly, from everything. The only unbiased stratum, and it
        cannot be replaced by the others: a doubt-ranked sample finds
        errors and can never say how many there are."""
        from explorer.models import Article

        rows = (
            Article.objects.using("crawler")
            .exclude(id__in=list(exclude))
            .values("id")
            .extra(select={"_d": "md5(articles.id)"})
            .order_by("_d")[:wanted]
        )
        return [{"article_id": r["id"]} for r in rows], {}

    def _unlabelled(self, wanted, exclude):
        """Articles the analysis stage has not reached.

        Empty while the crons are suspended and a stream once they run.
        The only stratum where a human label is training data outright:
        there is no model answer to agree or disagree with, so nothing
        biases it.
        """
        from explorer.models import Article

        rows = (
            Article.objects.using("crawler")
            .filter(primary_label__isnull=True)
            .exclude(id__in=list(exclude))
            .values("id")[:wanted]
        )
        return [{"article_id": r["id"]} for r in rows], {}

    # -------------------------------------------------------------- handle

    def handle(self, *args, **options):
        size, target = options["size"], options["target"]
        names = [n.strip() for n in options["coders"].split(",") if n.strip()]
        coders = list(User.objects.filter(username__in=names)) if names else []
        if names and len(coders) != len(names):
            missing = set(names) - {u.username for u in coders}
            raise CommandError(f"no such user: {', '.join(sorted(missing))}")
        if coders and len(coders) < target:
            # Arithmetically impossible, and worth refusing rather than
            # producing a cohort that can never reach its target.
            raise CommandError(
                f"{len(coders)} coders cannot give {target} apiece; "
                "add coders or lower --target"
            )

        drawn, probabilities, seen = [], {}, set()
        for stratum, share in SHARES.items():
            wanted = max(1, round(size * share))
            if stratum == ClassificationSample.CONFIDENT:
                rows, probs = self._labelled(
                    CONFIDENT_AT, 1.01, options["balance_labels"], wanted
                )
            elif stratum == ClassificationSample.UNCERTAIN:
                rows, probs = self._labelled(
                    -1.0, UNCERTAIN_BELOW, options["balance_labels"], wanted
                )
            elif stratum == ClassificationSample.RANDOM:
                rows, probs = self._random(wanted, seen)
            else:
                rows, probs = self._unlabelled(wanted, seen)

            for row in rows:
                key = row["article_id"]
                if key in seen:
                    continue
                seen.add(key)
                drawn.append((stratum, row))
                probabilities[key] = probs.get(key, 1.0)

        self.stdout.write(f"drew {len(drawn)} articles")
        for stratum in SHARES:
            n = sum(1 for s, _ in drawn if s == stratum)
            self.stdout.write(f"   {stratum:<12} {n}")

        if options["dry_run"]:
            self.stdout.write("dry run; nothing written")
            return

        with transaction.atomic():
            number = (
                ClassificationCohort.objects.order_by("-number")
                .values_list("number", flat=True)
                .first()
                or 0
            ) + 1
            cohort = ClassificationCohort.objects.create(
                number=number,
                slug=ClassificationCohort.slug_for(number),
                target_coders=target,
                note=options["note"],
            )
            ClassificationSample.objects.bulk_create(
                ClassificationSample(
                    cohort=cohort,
                    article_id=row["article_id"],
                    stratum=stratum,
                    inclusion_probability=probabilities.get(row["article_id"], 1.0),
                    model_label=row.get("primary_label", "") or "",
                    model_confidence=row.get("primary_label_confidence"),
                )
                for stratum, row in drawn
            )

            if coders:
                wheel = itertools.cycle(coders)
                assignments = []
                for _stratum, row in drawn:
                    # `target` distinct coders, taken off one rotating
                    # wheel so the load stays even across the cohort
                    # rather than even within each record.
                    chosen: list[User] = []
                    while len(chosen) < target:
                        nxt = next(wheel)
                        if nxt not in chosen:
                            chosen.append(nxt)
                    assignments.extend(
                        ClassificationAssignment(
                            cohort=cohort, article_id=row["article_id"], assigned_to=who
                        )
                        for who in chosen
                    )
                ClassificationAssignment.objects.bulk_create(assignments)
                self.stdout.write(
                    f"assigned {len(assignments)} across {len(coders)} coders"
                )

        self.stdout.write(f"{cohort} — grant it with scope {cohort.slug!r}")
