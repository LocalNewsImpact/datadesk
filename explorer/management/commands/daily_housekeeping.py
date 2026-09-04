"""The work the console needs done every day, in one place.

Two of these had no schedule at all. `refresh_worklist` fills the To Do
counts every reviewer's landing page reads, and had never run on a
schedule since it was built -- the counts were whatever the last manual
run left. `find_repeated_bodies` finds publishers whose parser returns
the same string for every article, and would have said nothing until
somebody remembered it existed.

One job rather than one per task. A second Cloud Run job and a second
Cloud Scheduler entry per task is how a task comes to have neither.

WHAT A FAILURE DOES
-------------------
Every task runs, whatever the ones before it did: a worklist count that
cannot be computed is no reason to skip the boilerplate scan, and they
touch different things. The command exits non-zero if any of them failed,
so the schedule shows red rather than a green run that did half the work.
"""

import time

from django.core.management import call_command
from django.core.management.base import BaseCommand

#: What runs, in order, with why it is here. Adding one is a line.
#:
#: Order is deliberate but not load-bearing: the worklist counts are what
#: somebody opens the console to see, so they are computed first and a
#: slow scan behind them cannot delay them.
TASKS = (
    (
        "refresh_worklist",
        "the To Do counts every landing page reads",
        (),
    ),
    (
        "find_repeated_bodies",
        "publishers whose parser returns the same string every time",
        (),
    ),
)


class Command(BaseCommand):
    help = "Run the console's daily tasks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            help="Run one task by name, for checking it by hand.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="Say what would run, and run nothing.",
        )

    def handle(self, *args, **options):
        tasks = TASKS
        if options["only"]:
            tasks = tuple(task for task in TASKS if task[0] == options["only"])
            if not tasks:
                known = ", ".join(name for name, _, _ in TASKS)
                raise SystemExit(f"no task {options['only']!r}; known: {known}")

        if options["list"]:
            for name, why, _ in tasks:
                self.stdout.write(f"  {name}: {why}")
            return

        failed = []
        for name, why, arguments in tasks:
            self.stdout.write(f"--- {name}: {why}")
            started = time.monotonic()
            try:
                call_command(name, *arguments)
            except Exception as exc:
                # Reported and carried on. These touch different things,
                # and one that cannot run is no reason to skip the rest.
                failed.append(name)
                self.stderr.write(self.style.ERROR(f"    {name} failed: {exc}"))
            else:
                self.stdout.write(f"    done in {time.monotonic() - started:.0f}s")

        if failed:
            self.stderr.write(
                self.style.ERROR(
                    f"{len(failed)} of {len(tasks)} failed: {', '.join(failed)}"
                )
            )
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(f"all {len(tasks)} tasks ran"))
