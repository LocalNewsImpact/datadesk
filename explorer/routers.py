"""Database routing for the read-only crawler connection."""


class CrawlerRouter:
    """Keep Django's hands off the crawler database.

    The real enforcement is Postgres: the connection runs as datadesk_ro,
    which can only SELECT (infra/sql/create_crawler_readonly_role.sql).
    This router is the local mirror of that contract — no app's migrations
    may land in the crawler alias, so `migrate` and test-database setup
    never try to create tables there.
    """

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == "crawler":
            return False
        return None
