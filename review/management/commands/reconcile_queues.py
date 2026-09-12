"""Make the records agree with what reviewers said about them.

Reports by default. `--apply` is required to write anything, because the
first run of this retracts 159 articles that are currently published as
local news, and a command that does that when somebody meant to look at
it is a command that gets run once and then distrusted.
"""

from django.core.management.base import BaseCommand, CommandError

from review import reconcile


class Command(BaseCommand):
    help = "Reconcile review dispositions with pipeline state"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Carry out the plan. Without it, nothing is written.",
        )
        parser.add_argument(
            "--actor",
            help=(
                "Email of the user the audit entries are recorded against. "
                "Required with --apply: an audited write path with no actor "
                "records a change nobody made."
            ),
        )

    def handle(self, *args, **options):
        plan = reconcile.build_plan()

        if not plan.changes and not plan.skipped:
            self.stdout.write("Nothing to reconcile.")
            return

        self.stdout.write(f"{len(plan.changes)} changes:")
        for rule, count in plan.by_rule().items():
            self.stdout.write(f"  {count:5d}  {rule}")

        retracted = plan.retracted()
        if retracted:
            # Said separately and loudly. Everything else here is
            # ordinary processing; this is a story ceasing to be
            # published as local news.
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"{len(retracted)} articles leave BigQuery "
                    "(they stop being published as local news)"
                )
            )

        if plan.skipped:
            self.stdout.write("")
            self.stdout.write(f"{len(plan.skipped)} skipped:")
            for pk, why in plan.skipped[:10]:
                self.stdout.write(f"  {pk}  {why}")
            if len(plan.skipped) > 10:
                self.stdout.write(f"  ... and {len(plan.skipped) - 10} more")

        if not options["apply"]:
            self.stdout.write("")
            self.stdout.write("Nothing written. Pass --apply to carry this out.")
            return

        if not options["actor"]:
            raise CommandError("--apply needs --actor: an audited write needs a writer")

        from django.contrib.auth.models import User

        actor = User.objects.filter(email=options["actor"]).first()
        if actor is None:
            raise CommandError(f"no user with email {options['actor']}")

        written = reconcile.apply_plan(plan, actor)
        self.stdout.write("")
        for rule, model, after, count, entry in written:
            self.stdout.write(
                f"  {count:5d}  {model} -> {after}  ({rule})  audit {entry}"
            )
        self.stdout.write(
            self.style.SUCCESS(f"{sum(w[3] for w in written)} rows reconciled")
        )
