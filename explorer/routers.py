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
        # Routed the same as reads so the failure is Postgres refusing the
        # write under datadesk_ro — loud and attributable — rather than a
        # phantom row landing in the default database.
        if getattr(model, "crawler_db", False):
            return "crawler"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        # Joins among crawler models are fine; nothing relates across the
        # two databases.
        if getattr(obj1, "crawler_db", False) and getattr(obj2, "crawler_db", False):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == "crawler":
            return False
        return None
