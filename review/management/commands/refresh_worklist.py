"""Count what is waiting, off the request path.

The counts are joins over the crawler's largest tables and one of them
takes 11.2 seconds against production. Nine of those is a landing page
nobody waits for, so they are counted here on a schedule and read from a
table.

Run alongside warm_caches, which already runs every 45 minutes as the
datadesk-warm Cloud Run job.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Recount the review worklist for every dataset."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dataset",
            help="One dataset slug. Default: every dataset.",
        )

    def handle(self, *args, **options):
        from explorer.models import Dataset
        from review import todo
        from review.models import WorklistCount

        started = timezone.now()
        datasets = Dataset.objects.all()
        if options.get("dataset"):
            datasets = datasets.filter(slug=options["dataset"])

        written = 0
        for dataset in datasets:
            for queue, count in todo.count_for_dataset(dataset).items():
                WorklistCount.objects.update_or_create(
                    dataset_slug=dataset.slug,
                    queue=queue,
                    defaults={"count": count},
                )
                written += 1
                self.stdout.write(f"  {dataset.slug:28} {queue:12} {count:>7}")

        # A queue that has been removed leaves a row behind saying work is
        # waiting in a place nobody can go.
        stale = WorklistCount.objects.exclude(
            dataset_slug__in=list(datasets.values_list("slug", flat=True))
        ).delete()

        seconds = (timezone.now() - started).total_seconds()
        self.stdout.write(
            f"{written} counts in {seconds:.1f}s"
            + (f", {stale[0]} stale rows removed" if stale[0] else "")
        )
