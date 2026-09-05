"""Find publishers whose parser returns the same boilerplate every time.

A parser that meets a page shape it does not handle returns the same
block for every article on the site -- a comment policy, a subscriber
wall, a list of counties.

WHAT THE TELL ACTUALLY IS
-------------------------
This grouped by exact character length, and that was wrong. Lengths
rarely repeat to the character; what repeats is the boilerplate itself,
with the surrounding text taking the total a few characters either way.
Grouping on `length(text)` finds the cases where a body is nothing BUT
the boilerplate and misses every case where a few characters of anything
else came with it -- which is most of them.

So the key is the boilerplate, fingerprinted: the opening of the body,
whitespace-collapsed and lowercased, hashed. Bodies whose first 160
characters are identical are the same failed capture whatever their
totals are, and the row records the range those totals span.

Both ends are looked at. A parser that returns only the boilerplate and
one that appends it to a stub produce it in different places, and a
fingerprint of the opening alone would find the first and not the
second.

    python manage.py find_boilerplate

Scheduled, not run per request: it reads 164,000 bodies.
"""

from django.core.management.base import BaseCommand
from django.db import connections
from django.utils import timezone

#: Below this a shared opening says nothing -- an empty body and a
#: two-word one repeat for ordinary reasons.
MIN_LENGTH = 100

#: No upper bound. There was one -- 5,000 characters -- and it made sense
#: only for the old key: two long articles can share an exact length by
#: coincidence, so long bodies were dropped. Sharing 160 characters of
#: opening or ending is not a coincidence, and the boilerplate a parser
#: emits arrives with anything from nothing to a whole story attached.
#: The difference in totals is not the signal and is not bounded: rows in
#: one pattern have been seen matching to the character and differing by
#: more than fifty.

#: How much of the body the fingerprint covers. Long enough that two
#: stories opening "The council met" do not collide; short enough that a
#: boilerplate block with a story after it still matches.
FINGERPRINT_CHARS = 160

#: How many articles sharing a fingerprint before it is a pattern rather
#: than an accident.
MIN_ARTICLES = 10

#: Normalised text: whitespace collapsed, lowercased. A parser that emits
#: the same block with different indentation on different pages is
#: emitting the same block.
#:
#: Only the ends are normalised, not the whole body. `regexp_replace`
#: over 164,000 full articles is the expensive part, and with no upper
#: bound on length it would be the whole corpus; the fingerprint only
#: ever reads 160 characters, so 400 either side is more than enough to
#: normalise first.
_NORMALISED_HEAD = r"lower(regexp_replace(left(a.text, 400), '\s+', ' ', 'g'))"
_NORMALISED_TAIL = r"lower(regexp_replace(right(a.text, 400), '\s+', ' ', 'g'))"

FIND = f"""
    WITH bodies AS (
        SELECT s.host                                  AS host,
               a.status                                AS status,
               length(a.text)                          AS chars,
               a.created_at                            AS created_at,
               left({_NORMALISED_HEAD}, %s)            AS opening,
               right({_NORMALISED_TAIL}, %s)           AS ending,
               left(a.text, 400)                       AS sample
        FROM articles a
        JOIN candidate_links cl ON cl.id = a.candidate_link_id
        JOIN sources s          ON s.id = cl.source_id
        WHERE a.text IS NOT NULL
          AND length(a.text) >= %s
    ),
    keyed AS (
        SELECT host, status, chars, created_at, sample,
               'opening' AS matched_on, md5(opening) AS fingerprint
        FROM bodies
        UNION ALL
        SELECT host, status, chars, created_at, sample,
               'ending' AS matched_on, md5(ending) AS fingerprint
        FROM bodies
    ),
    by_status AS (
        SELECT host, matched_on, fingerprint, status,
               count(*)              AS n,
               min(chars)            AS chars_min,
               max(chars)            AS chars_max,
               max(created_at)       AS latest,
               min(sample)           AS sample
        FROM keyed
        GROUP BY host, matched_on, fingerprint, status
    )
    SELECT host,
           matched_on,
           fingerprint,
           sum(n)::int                            AS articles,
           min(chars_min)::int                    AS chars_min,
           max(chars_max)::int                    AS chars_max,
           (array_agg(sample ORDER BY n DESC))[1] AS sample,
           max(latest)                            AS latest,
           jsonb_object_agg(status, n)            AS statuses
    FROM by_status
    GROUP BY host, matched_on, fingerprint
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


def _one_row_per_pattern(rows):
    """Drop the ending match when it is the same pattern as an opening one.

    A body that is nothing BUT the boilerplate has the same 160
    characters at both ends, so it is found twice -- once under each
    fingerprint -- and reported as two findings of the same articles.
    The opening row is kept because that is where a parser's failure
    usually starts.

    Two rows survive only when they are genuinely different: an opening
    match and an ending match covering different articles, which is a
    site producing two failures rather than one.
    """
    openings = {
        (row[0], row[3], (row[6] or "")[:120]) for row in rows if row[1] == "opening"
    }
    return [
        row
        for row in rows
        if row[1] == "opening" or (row[0], row[3], (row[6] or "")[:120]) not in openings
    ]


class Command(BaseCommand):
    help = "Find publishers whose article bodies repeat exactly."

    def add_arguments(self, parser):
        parser.add_argument("--min-articles", type=int, default=MIN_ARTICLES)
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        from review.models import Boilerplate

        started = timezone.now()
        with connections["crawler"].cursor() as cursor:
            cursor.execute(
                FIND,
                [
                    FINGERPRINT_CHARS,
                    FINGERPRINT_CHARS,
                    MIN_LENGTH,
                    options["min_articles"],
                ],
            )
            rows = cursor.fetchall()[: options["limit"]]

        rows = _one_row_per_pattern(rows)

        seen = []
        for (
            host,
            matched_on,
            fingerprint,
            articles,
            chars_min,
            chars_max,
            sample,
            latest,
            statuses,
        ) in rows:
            Boilerplate.objects.update_or_create(
                host=host or "unknown publisher",
                fingerprint=fingerprint,
                defaults={
                    "matched_on": matched_on,
                    "length": chars_min,
                    "length_max": chars_max,
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
            seen.append((host, fingerprint))

        # A pattern that no longer meets the threshold has been fixed, or
        # the articles were removed. Either way it is not a finding any
        # more, and leaving it would make this list a graveyard nobody
        # reads.
        gone = 0
        for stale in Boilerplate.objects.all():
            if (stale.host, stale.fingerprint) not in seen:
                stale.delete()
                gone += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{len(rows)} patterns, {gone} no longer found, "
                f"in {(timezone.now() - started).total_seconds():.0f}s"
            )
        )
