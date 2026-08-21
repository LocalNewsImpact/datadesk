"""Database routing for the read-only crawler connection."""


class CrawlerRouter:
    """Keep Django's hands off the crawler database.

    The real enforcement is Postgres: the connection runs as datadesk_ro,
    which can only SELECT (infra/sql/create_crawler_readonly_role.sql).
    This router is the local mirror of that contract — no app's migrations
    may land in the crawler alias, so `migrate` and test-database setup
    never try to create tables there.
    """

    def db_for_read(self, model, **hints):
        if getattr(model, "crawler_db", False):
            return "crawler"
        return None

    def db_for_write(self, model, **hints):
        # Writes go through the audited datadesk_rw alias when configured
        # (SCOPE.md §6.5). Without it (development, tests) they fall
        # through to the crawler alias so the failure in production would
        # be Postgres refusing datadesk_ro — loud and attributable — and
        # the write path stays testable against sqlite locally.
        from django.conf import settings

        if getattr(model, "crawler_db", False):
            if "crawler_rw" in settings.DATABASES:
                return "crawler_rw"
            return "crawler"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        # Joins among crawler models are fine; nothing relates across the
        # two databases.
        if getattr(obj1, "crawler_db", False) and getattr(obj2, "crawler_db", False):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db in ("crawler", "crawler_rw"):
            return False
        return None
