"""Rank the database's own queries by what they actually cost.

Request logs are the weakest signal available for this. They are sampled
by whatever somebody happened to click, they attribute time to a URL
rather than to a query, and they say nothing at all about a query that is
fast on its own and issued four hundred times in one request.

`pg_stat_statements` has none of those problems: the server counts every
execution of every statement, so ranking by TOTAL time surfaces both the
one query that takes four seconds and the one that takes four milliseconds
ten thousand times. That second shape is an N+1, and it is invisible to a
latency percentile.

The extension is already in `shared_preload_libraries` on the shared Cloud
SQL instance; it just has to be created once per database. This command
says so rather than failing, because "not installed" and "installed but
nothing slow" must not look alike.

    python manage.py slow_queries --database crawler
    python manage.py slow_queries --order calls     # find the N+1s
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connections

#: Statements that are noise in this report: the extension's own reads,
#: and the per-connection bookkeeping every client issues.
_SKIP = (
    "pg_stat_statements",
    "SET ",
    "SHOW ",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
)

_ORDERS = {
    "total": "total_exec_time DESC",
    "mean": "mean_exec_time DESC",
    "calls": "calls DESC",
    "rows": "rows DESC",
}


class Command(BaseCommand):
    help = "Rank queries by measured cost, from pg_stat_statements."

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="crawler",
            help="Database alias to inspect (default: crawler).",
        )
        parser.add_argument(
            "--order",
            default="total",
            choices=sorted(_ORDERS),
            help=(
                "total: what costs the most overall (the default, and the "
                "right first question). mean: the slowest single statements. "
                "calls: the N+1 candidates."
            ),
        )
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument(
            "--min-calls",
            type=int,
            default=1,
            help="Ignore statements called fewer times than this.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help=(
                "Zero the statistics after reporting, so the next run "
                "measures a known window rather than all of history."
            ),
        )

    def handle(self, *args, **options):
        alias = options["database"]
        connection = connections[alias]
        with connection.cursor() as cursor:
            if not self._available(cursor):
                self._explain_how_to_enable(cursor, alias)
                return
            cursor.execute(
                f"""
                SELECT calls,
                       round(mean_exec_time::numeric, 1),
                       round(total_exec_time::numeric / 1000, 1),
                       rows,
                       query
                FROM pg_stat_statements
                WHERE calls >= %s
                ORDER BY {_ORDERS[options["order"]]}
                LIMIT %s
                """,
                [options["min_calls"], options["limit"] * 3],
            )
            rows = [r for r in cursor.fetchall() if not self._noise(r[4])]

        if not rows:
            self.stdout.write(
                "pg_stat_statements is installed but has recorded nothing yet. "
                "It fills as the application is used."
            )
            return

        self.stdout.write(
            f"{'calls':>9} {'mean ms':>9} {'total s':>9} {'rows':>10}  query"
        )
        self.stdout.write("-" * 110)
        for calls, mean_ms, total_s, nrows, query in rows[: options["limit"]]:
            self.stdout.write(
                f"{calls:>9} {mean_ms:>9} {total_s:>9} {nrows:>10}  "
                f"{' '.join(query.split())[:70]}"
            )
        self.stdout.write("")
        self.stdout.write(
            "A large `calls` with a small `mean ms` and a large `total s` is an "
            "N+1: fix it with select_related/prefetch_related, not an index."
        )

        if options["reset"]:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_stat_statements_reset()")
            self.stdout.write("Statistics reset.")

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _noise(query: str) -> bool:
        return any(query.lstrip().startswith(s) or s in query for s in _SKIP)

    @staticmethod
    def _available(cursor) -> bool:
        cursor.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"
        )
        return cursor.fetchone() is not None

    def _explain_how_to_enable(self, cursor, alias: str) -> None:
        """Say which of the two reasons it is unavailable, and what to do.

        Preloaded-but-not-created is one statement away. Not preloaded needs
        an instance flag and a restart, which is a different conversation.
        """
        cursor.execute("SHOW shared_preload_libraries")
        preloaded = "pg_stat_statements" in (cursor.fetchone()[0] or "")
        self.stdout.write(
            self.style.WARNING(
                f"pg_stat_statements is not created in the '{alias}' database."
            )
        )
        if preloaded:
            self.stdout.write(
                "It IS preloaded on this instance, so it needs one statement, "
                "no restart:\n\n"
                "    CREATE EXTENSION IF NOT EXISTS pg_stat_statements;\n\n"
                "It collects from that moment on; give it a few days of real "
                "traffic before drawing conclusions."
            )
        else:
            self.stdout.write(
                "It is not in shared_preload_libraries either. On Cloud SQL "
                "that is the `cloudsql.enable_pg_stat_statements` flag, and "
                "setting it restarts the instance."
            )
