"""Find publishers whose parser returns the same thing every time.

A parser that meets a page shape it does not handle returns one string
for every article on the site -- a comment policy, a subscriber wall, a
list of counties -- and the tell is that the body length repeats exactly.

On 2026-09-04, before anybody had reported one: 486 articles from
newspressnow.com at exactly 228 characters, every one of them the site's
comment policy, and 472 of those classified `wire`. A failed capture
recorded as syndication, 486 times.

    python manage.py find_repeated_bodies

Scheduled, not run per request: grouping 164,000 articles by host and
length takes about 32 seconds against the crawler's database.
"""

from django.core.management.base import BaseCommand
from django.db import connections
from django.utils import timezone

#: Below this a repeated length says nothing -- an empty body and a
#: two-word one repeat for ordinary reasons.
MIN_LENGTH = 100

#: Above it, an exact repeat is a coincidence a real corpus does produce:
#: two long articles can share a length. Boilerplate is short.
MAX_LENGTH = 5000

#: How many articles sharing an exact length before it is a pattern
#: rather than an accident.
MIN_ARTICLES = 10

FIND = """
    WITH by_status AS (
        SELECT s.host                AS host,
               length(a.text)        AS chars,
               a.status              AS status,
               count(*)              AS n,
               max(a.created_at)     AS latest,
               min(left(a.text, 400)) AS sample
        FROM articles a
        JOIN candidate_links cl ON cl.id = a.candidate_link_id
        JOIN sources s          ON s.id = cl.source_id
        WHERE a.text IS NOT NULL
          AND length(a.text) BETWEEN %s AND %s
        GROUP BY s.host, length(a.text), a.status
    )
    SELECT host,
           chars,
           sum(n)::int                            AS articles,
           (array_agg(sample ORDER BY n DESC))[1] AS sample,
           max(latest)                            AS latest,
           jsonb_object_agg(status, n)            AS statuses
    FROM by_status
    GROUP BY host, chars
    HAVING sum(n) >= %s
    ORDER BY sum(n) DESC
"""


def _as_mapping(value):
    """`jsonb_object_agg`, whatever the driver returned it as."""
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


class Command(BaseCommand):
    help = "Find publishers whose article bodies repeat exactly."

    def add_arguments(self, parser):
        parser.add_argument("--min-articles", type=int, default=MIN_ARTICLES)
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        from review.models import RepeatedBody

        started = timezone.now()
        with connections["crawler"].cursor() as cursor:
            cursor.execute(FIND, [MIN_LENGTH, MAX_LENGTH, options["min_articles"]])
            rows = cursor.fetchall()[: options["limit"]]

        seen = []
        for host, length, articles, sample, latest, statuses in rows:
            RepeatedBody.objects.update_or_create(
                host=host or "unknown publisher",
                length=length,
                defaults={
                    "articles": articles,
                    "sample": (sample or "").strip()[:400],
                    # A raw cursor hands `jsonb_object_agg` back as text
                    # on some drivers and as a dict on others. Stored
                    # unparsed it becomes a JSONField holding a string,
                    # and every reader gets a str where it expects a
                    # mapping -- which is the same confusion between
                    # `json` and text that took the review queue down.
                    "statuses": _as_mapping(statuses),
                    "latest_article": latest,
                },
            )
            seen.append((host, length))

        # A pattern that no longer meets the threshold has been fixed, or
        # the articles were removed. Either way it is not a finding any
        # more, and leaving it would make this list a graveyard nobody
        # reads.
        gone = 0
        for stale in RepeatedBody.objects.all():
            if (stale.host, stale.length) not in seen:
                stale.delete()
                gone += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{len(rows)} patterns, {gone} no longer found, "
                f"in {(timezone.now() - started).total_seconds():.0f}s"
            )
        )
