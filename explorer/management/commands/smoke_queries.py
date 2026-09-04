"""Run the read paths against the real databases and report what breaks.

`/_health` proves Django started. It does not touch the crawler, so a
query that cannot run against the real schema deploys green and fails
when somebody opens the page -- which is how the review queue shipped
telling people their credentials were wrong.

This runs each expensive read the console does, against whatever the
`crawler` alias points at, and fails if any of them raises. Run against
the candidate revision before traffic shifts, it keeps a broken query
off the site; run by hand through the Cloud SQL Auth Proxy, it answers
"would this deploy work" without a browser.

    python manage.py smoke_queries

It reads. It writes nothing, and the alias it reads through is
SELECT-only besides.
"""

import traceback

from django.core.management.base import BaseCommand
from django.db import connections


def _checks():
    """Every read path worth proving, as (name, callable).

    Imported inside so that one import failure is one failed check
    rather than a command that will not start.
    """
    from django.contrib.auth import get_user_model

    from explorer import costs, crawler, dashboard, views
    from explorer.models import Dataset
    from review import queue as review_queue
    from review import todo

    def _somebody():
        """A real user, because access narrowing is part of the query.

        The scoped paths join through group membership, so running them
        as nobody proves less. Where there is no user at all -- an empty
        application database -- this falls back to AnonymousUser, and the
        command says so rather than reporting a hollow pass.
        """
        from django.contrib.auth.models import AnonymousUser

        user = get_user_model().objects.filter(is_active=True).order_by("pk").first()
        return user or AnonymousUser()

    checks = [
        ("crawler row counts", crawler.dataset_row_counts),
        ("dashboard corpus summary", dashboard.corpus_summary),
        ("recorded costs", costs.recorded_costs),
        ("billed costs", costs.billed_costs),
        ("article filter vocabulary", lambda: views._filter_vocab()),
        ("enrichment filter vocabulary", lambda: views._enrichment_vocab()),
        ("review queue vocabulary", lambda: review_queue.vocab(_somebody())),
        (
            "review queue, first page",
            lambda: list(review_queue.queued({}, _somebody())[:25]),
        ),
        (
            "review queue, one case",
            lambda: list(
                review_queue.queued({"case": "scope_mislabel"}, _somebody())[:25]
            ),
        ),
        (
            "flagged rows, unscoped",
            lambda: review_queue.base_queryset_unscoped().count(),
        ),
    ]

    for dataset in Dataset.objects.order_by("slug")[:5]:
        checks.append(
            (
                f"worklist counts, {dataset.slug}",
                lambda d=dataset: todo.count_for_dataset(d),
            )
        )
    return checks


#: Modules a view imports inside the request, where a dependency missing
#: from the image is a 500 on that page and nothing anywhere else. This is
#: not hypothetical: review.dispositions imports lnic_contracts at module
#: scope, the package was in requirements-dev.txt and not requirements.txt,
#: and /review/queue/ raised ModuleNotFoundError in production while every
#: test and every local reproduction passed.
DEFERRED_IMPORTS = (
    "review.dispositions",
    "review.queue",
    "review.todo",
    "review.views",
    "explorer.views",
    "explorer.dashboard",
    "explorer.costs",
    "explorer.crawler",
    "datasets.views",
    "visuals.views",
    "visuals.builder",
    "visuals.panels",
    "accounts.access",
    "audit.models",
)


def _unimportable():
    """Every module in DEFERRED_IMPORTS this image cannot import."""
    import importlib

    failures = []
    for name in DEFERRED_IMPORTS:
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures.append((name, exc))
    return failures


class Command(BaseCommand):
    help = "Run the console's read paths against the real databases."

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-sqlite",
            action="store_true",
            help="Run even where the crawler alias is not Postgres.",
        )

    def handle(self, *args, **options):
        vendor = connections["crawler"].vendor
        if vendor != "postgresql" and not options["allow_sqlite"]:
            raise SystemExit(
                f"The crawler alias is {vendor}. This proves nothing about "
                "production; point CRAWLER_DB_USER at the real database "
                "(Cloud SQL Auth Proxy locally) or pass --allow-sqlite."
            )

        missing = _unimportable()
        if missing:
            for where, exc in missing:
                self.stderr.write(self.style.ERROR(f"FAIL  import {where}: {exc}"))
            self.stderr.write(
                self.style.ERROR(
                    f"{len(missing)} modules the views import cannot be "
                    "imported in this image"
                )
            )
            raise SystemExit(1)

        try:
            checks = _checks()
        except Exception:
            self.stderr.write(self.style.ERROR("could not assemble the checks:"))
            self.stderr.write(traceback.format_exc())
            raise SystemExit(1) from None

        from django.contrib.auth import get_user_model

        if not get_user_model().objects.filter(is_active=True).exists():
            self.stdout.write(
                self.style.WARNING(
                    "no active user: the scoped read paths run as nobody and "
                    "narrow to nothing, which proves less than a real deploy."
                )
            )

        failed = []
        for name, run in checks:
            try:
                run()
            except Exception as exc:
                failed.append((name, exc))
                self.stderr.write(self.style.ERROR(f"FAIL  {name}: {exc}"))
                self.stderr.write(traceback.format_exc())
            else:
                self.stdout.write(f"ok    {name}")

        if failed:
            self.stderr.write(
                self.style.ERROR(
                    f"{len(failed)} of {len(checks)} read paths failed: "
                    + ", ".join(name for name, _ in failed)
                )
            )
            raise SystemExit(1)
        self.stdout.write(
            self.style.SUCCESS(f"all {len(checks)} read paths ran against {vendor}")
        )
