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
        # BEFORE the plan, not after. A run with nothing to do used to
        # return early and never reach this check, so a schedule whose
        # actor was wrong looked healthy on every quiet night and failed
        # only once there was work -- which is the night you would rather
        # it did not.
        actor = None
        if options["apply"]:
            actor = self._actor(options.get("actor"))

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

        written = reconcile.apply_plan(plan, actor)
        self.stdout.write("")
        for rule, model, after, count, entry in written:
            self.stdout.write(
                f"  {count:5d}  {model} -> {after}  ({rule})  audit {entry}"
            )
        self.stdout.write(
            self.style.SUCCESS(f"{sum(w[3] for w in written)} rows reconciled")
        )

    def _actor(self, email):
        """The user every change is recorded against.

        An audited write with no writer records a change nobody made. On
        a schedule there is no person, so the job names its own service
        account and `review/migrations/0020` creates that account --
        inactive, so it is a name in an audit trail rather than a login.
        """
        from django.contrib.auth.models import User

        if not email:
            raise CommandError("--apply needs --actor: an audited write needs a writer")
        actor = User.objects.filter(email=email).first()
        if actor is None:
            raise CommandError(
                f"no user with email {email}. A scheduled run names its "
                "service account, which review/migrations/0020 creates."
            )
        return actor
