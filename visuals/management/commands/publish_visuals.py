"""Refresh the snapshots behind published visuals (SCOPE.md §2.7 v1).

This is the command the publish workflow runs. It is deliberately not the
same act as publishing:

    refresh   take a new snapshot version from the visual's data source
    publish   pin a snapshot version, so embeds serve that exact data

SCOPE.md's embed-stability rule is that "a published report must not
change under its readers", so a scheduled job may not move the pin. It
takes a fresh version and stops; the pin moves only when a person
publishes in the console, or when someone dispatches this command with
--repin and says why.

The audit log records who did a thing (SCOPE.md §2.1), so the command
needs an actor and refuses to invent one: --actor is an existing account
holding the admin role.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.access import is_application_admin
from accounts.decorators import APP
from visuals.models import Visual
from visuals.services import DataSourceError, publish, refresh_snapshot


class Command(BaseCommand):
    help = "Refresh snapshots for published visuals; optionally re-pin them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--actor",
            required=True,
            help="Email of the account the refresh is recorded against.",
        )
        parser.add_argument(
            "--slug",
            action="append",
            default=[],
            help="Limit to these visuals (repeatable). Default: all published.",
        )
        parser.add_argument(
            "--repin",
            action="store_true",
            help=(
                "Also move each visual's pin to the new snapshot. Changes what "
                "existing embeds serve, so it is never the scheduled default."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be refreshed and write nothing.",
        )

    def handle(self, *args, **options):
        actor = self._actor(options["actor"])
        visuals = Visual.objects.filter(status=Visual.PUBLISHED)
        if options["slug"]:
            visuals = visuals.filter(slug__in=options["slug"])
        visuals = visuals.order_by("slug")

        if not visuals:
            self.stdout.write("No published visuals to refresh.")
            return

        failures = []
        for visual in visuals:
            pinned = visual.pinned_snapshot.version if visual.pinned_snapshot else None
            # Read before the refresh: `refresh_snapshot` does not move
            # the pin, but reading after it invites the next author to
            # reorder these two lines and compare a thing with itself.
            pinned_data = (
                visual.pinned_snapshot.data if visual.pinned_snapshot else None
            )
            if options["dry_run"]:
                would = (
                    "refresh and repin if the data changed"
                    if options["repin"] or visual.keep_updated
                    else "refresh"
                )
                self.stdout.write(f"{visual.slug}: would {would} (pinned v{pinned})")
                continue
            try:
                snapshot = refresh_snapshot(visual, actor)
            except DataSourceError as exc:
                # One unreachable source must not stop the rest; the
                # command still exits non-zero so the run is not green.
                failures.append(f"{visual.slug}: {exc}")
                self.stderr.write(f"{visual.slug}: {exc}")
                continue
            # `--repin` is the operator saying so for this run; a visual
            # marked keep_updated says so for itself, for ever. The flag
            # still wins where it is given, so one run can repin
            # everything without anybody editing a visual.
            asked = options["repin"] or visual.keep_updated
            # Only when it actually changed. A daily republish of
            # identical rows moves the pin, writes an audit entry and
            # invalidates an hour of cache to deliver the same numbers --
            # and it buries the entries that meant something.
            changed = pinned_data != snapshot.data
            if asked and changed:
                publish(visual, actor)
                self.stdout.write(
                    f"{visual.slug}: v{snapshot.version} taken and pinned "
                    f"(was v{pinned})"
                )
            elif asked:
                self.stdout.write(
                    f"{visual.slug}: v{snapshot.version} taken; same data, "
                    f"pin stays at v{pinned}"
                )
            else:
                self.stdout.write(
                    f"{visual.slug}: v{snapshot.version} taken; embeds still "
                    f"serve v{pinned}"
                )

        if failures:
            raise CommandError(
                f"{len(failures)} visual(s) could not be refreshed:\n"
                + "\n".join(failures)
            )

    def _actor(self, email):
        user = get_user_model().objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"No account with the email {email}.")
        if not is_application_admin(user, APP):
            raise CommandError(
                f"{email} does not hold the admin role; a snapshot is an "
                "audited action and must be attributable to someone who "
                "could have taken it in the console."
            )
        return user
