"""Load the 2023 V1 coding: its stories, and what each coder said.

    manage.py import_v1_stories <file-or-folder> [<file-or-folder> ...]

Give it the five `Missouri_News_Labeling (Group A..E)` JotForm exports,
or the folders they are in. Idempotent: running it again replaces the
V1 answers and updates the stories, and changes nothing else.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from review import v1_import
from review.classification import ClassificationStory, V1Decision


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("paths", nargs="+")
        parser.add_argument(
            "--dry-run", action="store_true", help="Read and report; write nothing."
        )

    def handle(self, *args, paths, dry_run=False, **options):
        files = []
        for given in paths:
            path = Path(given).expanduser()
            found = (
                sorted(path.glob("Missouri_News_Labeling*Group*.csv"))
                if path.is_dir()
                else [path]
            )
            files += [f for f in found if v1_import.group_of(f)]
        groups = sorted({v1_import.group_of(f) for f in files})
        if not files:
            raise CommandError("No Missouri_News_Labeling Group A-E files found.")
        self.stdout.write(f"{len(files)} files, groups {', '.join(groups)}")
        if groups != list("ABCDE"):
            # Half the files is half the coders: every outcome computed
            # from it would be wrong, and would look complete.
            raise CommandError(
                "All five groups are needed; found " + ", ".join(groups) + "."
            )

        stories = v1_import.collect(files)
        rows, decisions, tally = [], [], {}
        for story_id, entry in stories.items():
            fields, answers = entry["story"], entry["answers"]
            result, label = v1_import.outcome(answers)
            tally[result] = tally.get(result, 0) + 1
            rows.append(
                ClassificationStory(
                    id=f"v1:{story_id}",
                    origin=ClassificationStory.V1,
                    headline=fields["headline"],
                    body=fields["body"],
                    url=fields["url"],
                    host=v1_import.host_of(fields["url"]),
                    source_hash=fields["hash"][:64],
                    v1_outcome=result,
                    v1_label=label,
                    civic=v1_import.is_civic(answers),
                )
            )
            for answer in answers:
                primary, reject = v1_import.split_answer(answer["primary"])
                decisions.append(
                    V1Decision(
                        story_id=f"v1:{story_id}",
                        group=answer["group"],
                        primary_label=primary,
                        secondary_label=answer["secondary"],
                        reject_reason=reject,
                        also_present=answer["also"],
                    )
                )

        kept = [r for r in rows if r.v1_outcome != ClassificationStory.REJECTED]
        self.stdout.write(
            f"{len(rows)} stories, {len(decisions)} answers; "
            + ", ".join(f"{k} {v}" for k, v in sorted(tally.items()))
        )
        self.stdout.write(
            f"not rejected: {len(kept)}; civic among them: "
            f"{sum(r.civic for r in kept)}; without text: "
            f"{sum(not r.body for r in rows)}"
        )
        if dry_run:
            self.stdout.write("dry run: nothing written")
            return
        with transaction.atomic():
            ClassificationStory.objects.bulk_create(
                rows,
                batch_size=200,
                update_conflicts=True,
                unique_fields=["id"],
                update_fields=[
                    "origin",
                    "headline",
                    "body",
                    "url",
                    "host",
                    "source_hash",
                    "v1_outcome",
                    "v1_label",
                    "civic",
                ],
            )
            V1Decision.objects.filter(story__origin=ClassificationStory.V1).delete()
            V1Decision.objects.bulk_create(decisions, batch_size=500)
        self.stdout.write("written")
