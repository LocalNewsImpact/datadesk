"""Everything standing between a discovered URL and a finished article.

The queues review judgements: this reports failures. Nothing here is a
verdict somebody should accept or reject -- a 404 is not an opinion --
and the useful answer is a retry, a fetch strategy, or a source that
should be retired. So it is a report, not a queue, and it carries no
verbs.

The unit is the publisher wherever a publisher explains it. 6,467 West
Plains Daily Quill links did not each fail on their own: the site
returns 403 and the crawler stopped asking. A list of 6,467 rows asks a
reviewer to read the same fact 6,467 times; one row saying which site,
how many, and why is the same information in the form somebody can act
on.

WHAT COUNTS AS BLOCKED
----------------------
An article is blocked when a stage will not pick it up and no later
stage will either. That is deliberately wider than "errored": a link
held at `paused`, a source switched off, a body that arrived as
ciphertext and a run waiting on a suspended cron all end the same way,
with nothing in the export and nothing saying so.

A backlog is not a failure, and the difference is worth keeping visible:
`waiting` counts what is queued and will move when the pipeline runs,
`blocked` counts what will not move until somebody changes something.
"""

from django.db import connections

#: Held before anything was fetched.
NEVER_FETCHED = "never fetched"
#: Fetched, and nothing usable came back.
FETCH_FAILED = "fetch failed"
#: Extracted, then stopped before the next stage.
STALLED = "stalled"
#: Captured, but the body cannot be used.
BODY_UNUSABLE = "body unusable"
#: Queued and moving when the pipeline next runs. Not a failure.
WAITING = "waiting"

#: (group, label, why it matters, SQL) -- one row of the report each.
#:
#: Written as SQL rather than through the unmanaged models because most
#: of these are candidate-link facts and telemetry, which the explorer's
#: models reach only through joins that would obscure what is being
#: counted. The report's whole value is that a reader can see the
#: question.
CHECKS = (
    (
        NEVER_FETCHED,
        "Paused after repeated 403s",
        "The site refused often enough that the crawler stopped asking.",
        "SELECT count(*) FROM candidate_links "
        "WHERE status = 'paused' AND error_message LIKE '%%403%%'",
    ),
    (
        NEVER_FETCHED,
        "Paused with no reason recorded",
        "Held, and the row does not say what stopped it.",
        "SELECT count(*) FROM candidate_links "
        "WHERE status = 'paused' AND error_message IS NULL",
    ),
    (
        NEVER_FETCHED,
        "Verified, never extracted",
        "Ready for extraction and still waiting for a run to claim it.",
        "SELECT count(*) FROM candidate_links cl WHERE cl.status = 'article' "
        "AND NOT EXISTS "
        "(SELECT 1 FROM articles a WHERE a.candidate_link_id = cl.id)",
    ),
    (
        NEVER_FETCHED,
        "Link on a switched-off source",
        "Both extraction selectors require an active source, so these are "
        "skipped silently -- a restore here does nothing and says nothing.",
        "SELECT count(*) FROM candidate_links cl "
        "JOIN sources so ON so.id = cl.source_id "
        "WHERE cl.status = 'article' AND so.status IN ('paused', 'retired')",
    ),
    (
        FETCH_FAILED,
        "404 not found",
        "The URL is gone. Discovery found it; the page no longer exists.",
        "SELECT count(DISTINCT url) FROM extraction_telemetry_v2 "
        "WHERE http_status_code = 404",
    ),
    (
        FETCH_FAILED,
        "403 refused",
        "Reached the site and was turned away.",
        "SELECT count(DISTINCT url) FROM extraction_telemetry_v2 "
        "WHERE http_status_code = 403",
    ),
    (
        FETCH_FAILED,
        "429 or server error",
        "Asked too fast, or the site broke.",
        "SELECT count(DISTINCT url) FROM extraction_telemetry_v2 "
        "WHERE http_status_code = 429 OR http_status_code >= 500",
    ),
    (
        FETCH_FAILED,
        "Proxy challenge",
        "A bot wall answered instead of the page. Counted apart from a 403 "
        "because the remedy is the fetch method, not the schedule.",
        "SELECT count(DISTINCT url) FROM extraction_telemetry_v2 "
        "WHERE error_type = 'proxy_challenge'",
    ),
    (
        FETCH_FAILED,
        "Proxy blocked",
        "The proxy itself refused the request.",
        "SELECT count(DISTINCT url) FROM extraction_telemetry_v2 "
        "WHERE error_type = 'proxy_blocked'",
    ),
    (
        STALLED,
        "Stuck at 'cleaned' over 24h",
        "Extracted and never labelled. Labelling promotes 'cleaned'; "
        "anything sitting here means that stage is not running.",
        "SELECT count(*) FROM articles WHERE status = 'cleaned' "
        "AND extracted_at < NOW() - INTERVAL '24 hours'",
    ),
    (
        STALLED,
        "Enrichment attempts exhausted",
        "Tried the limit of times and failed each time.",
        "SELECT count(*) FROM articles "
        "WHERE enrichment_attempts >= 3 AND status = 'labeled'",
    ),
    (
        STALLED,
        "Held for review",
        "Stopped on purpose, waiting on a person. Selected by no stage, "
        "so it moves only when somebody decides it.",
        "SELECT count(*) FROM articles WHERE status = 'in_review'",
    ),
    (
        STALLED,
        "Article paused",
        "Taken out of the pipeline by a reviewer reporting a bad body.",
        "SELECT count(*) FROM articles WHERE status = 'paused'",
    ),
    (
        BODY_UNUSABLE,
        "Body is still ROT47 ciphertext",
        "TownNews serves paywalled prose rotated. The decoder ran and the "
        "result did not pass its own check, so ciphertext was stored as text.",
        "SELECT count(*) FROM articles WHERE content LIKE '%%k^Am%%'",
    ),
    (
        BODY_UNUSABLE,
        "No body at all",
        "An article row with nothing in content, text or excerpt.",
        "SELECT count(*) FROM articles "
        "WHERE coalesce(content, text, text_excerpt, '') = ''",
    ),
    (
        WAITING,
        "Labelled, waiting on enrichment",
        "Queued and correct. This moves on its own when the enrichment "
        "cron runs, and stays here while the crons are suspended.",
        "SELECT count(*) FROM articles a "
        "LEFT JOIN article_enrichment e ON e.article_id = a.id "
        "WHERE a.status = 'labeled' AND e.article_id IS NULL",
    ),
)

#: Which publishers account for a blockage, for the ones a publisher explains.
BY_PUBLISHER = {
    "Paused after repeated 403s": (
        "SELECT coalesce(so.canonical_name, so.host, cl.source) AS publisher, count(*) "
        "FROM candidate_links cl LEFT JOIN sources so ON so.id = cl.source_id "
        "WHERE cl.status = 'paused' AND cl.error_message LIKE '%%403%%' "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 8"
    ),
    "Proxy challenge": (
        "SELECT coalesce(so.canonical_name, t.publisher) AS publisher, "
        "count(DISTINCT t.url) FROM extraction_telemetry_v2 t "
        "LEFT JOIN candidate_links cl ON cl.id = t.candidate_link_id "
        "LEFT JOIN sources so ON so.id = cl.source_id "
        "WHERE t.error_type = 'proxy_challenge' GROUP BY 1 ORDER BY 2 DESC LIMIT 8"
    ),
    "404 not found": (
        "SELECT coalesce(so.canonical_name, t.publisher) AS publisher, "
        "count(DISTINCT t.url) FROM extraction_telemetry_v2 t "
        "LEFT JOIN candidate_links cl ON cl.id = t.candidate_link_id "
        "LEFT JOIN sources so ON so.id = cl.source_id "
        "WHERE t.http_status_code = 404 GROUP BY 1 ORDER BY 2 DESC LIMIT 8"
    ),
    "Body is still ROT47 ciphertext": (
        "SELECT coalesce(so.canonical_name, so.host) AS publisher, count(*) "
        "FROM articles a LEFT JOIN candidate_links cl ON cl.id = a.candidate_link_id "
        "LEFT JOIN sources so ON so.id = cl.source_id "
        "WHERE a.content LIKE '%%k^Am%%' GROUP BY 1 ORDER BY 2 DESC LIMIT 8"
    ),
}


def _scalar(cursor, sql):
    cursor.execute(sql)
    row = cursor.fetchone()
    return row[0] if row else 0


def inventory():
    """Every blockage, counted, or None where the crawler is unreachable.

    One connection and one pass. A page that opens fifteen connections to
    say fifteen numbers is a page nobody leaves open.
    """
    from django.db import DatabaseError

    from explorer.dberrors import absent_or_raise

    try:
        with connections["crawler"].cursor() as cursor:
            found = []
            for group, label, why, sql in CHECKS:
                count = _scalar(cursor, sql)
                publishers = []
                if count and label in BY_PUBLISHER:
                    cursor.execute(BY_PUBLISHER[label])
                    publishers = [
                        {"publisher": name or "unattributed", "count": n}
                        for name, n in cursor.fetchall()
                    ]
                found.append(
                    {
                        "group": group,
                        "label": label,
                        "why": why,
                        "count": count,
                        "publishers": publishers,
                    }
                )
            return found
    except DatabaseError as exc:
        absent_or_raise(exc, "explorer.blocked.inventory")
        return None


def grouped():
    """The inventory as the page shows it: by group, worst first inside.

    `waiting` is kept last and apart. It is the largest number on the
    page and it is not a fault, so putting it in rank order with the
    failures would make the page read as though the pipeline were broken
    when it is merely switched off.
    """
    rows = inventory()
    if rows is None:
        return None
    order = (NEVER_FETCHED, FETCH_FAILED, STALLED, BODY_UNUSABLE, WAITING)
    out = []
    for group in order:
        in_group = [r for r in rows if r["group"] == group and r["count"]]
        if in_group:
            out.append(
                {
                    "group": group,
                    "total": sum(r["count"] for r in in_group),
                    "rows": sorted(in_group, key=lambda r: -r["count"]),
                }
            )
    return out


def blocked_total():
    """Everything that will not move until somebody changes something."""
    rows = inventory()
    if rows is None:
        return None
    return sum(r["count"] for r in rows if r["group"] != WAITING)
