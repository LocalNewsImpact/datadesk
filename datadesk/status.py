"""A status page to bookmark: is the corpus moving, and how far along.

NOT WIRED INTO THE NAVIGATION, and that is the point. This is a URL to
open on a phone while a long re-extraction runs in the cluster -- one
screen, a handful of numbers, no chrome to tap through. It answers the
question somebody actually asks from the road: is it still going, and
how much is left.

It reads the crawler's own tables rather than the cluster. A workflow
that reports Running having processed nothing is not progress, and a
workflow that died after finishing is not a failure; the count of work
outstanding is the honest signal either way, and it survives the
workflow being deleted.

The window is a query parameter, so the page outlives this campaign:
`?since=2026-09-16T14:00:06Z` counts work done after that moment. The
default is the last 24 hours, which is the right question on any
ordinary day.
"""

from datetime import UTC, datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connections
from django.shortcuts import render

# Extraction commits per source, so a gap between commits is normal. A
# quarter hour without one is not: that is a dead run, not a slow batch.
STALE_AFTER = timedelta(minutes=15)

_SQL = """
    SELECT
        count(*) FILTER (
            WHERE a.entities_extracted_at >= %(since)s
        ) AS done,
        count(*) FILTER (
            WHERE a.entities_extracted_at IS NULL
               OR a.entities_extracted_at < %(since)s
        ) AS remaining,
        max(a.entities_extracted_at) AS latest
    FROM articles a
    WHERE EXISTS (
        SELECT 1 FROM article_enrichment e WHERE e.article_id = a.id
    )
"""


def _parse_since(raw):
    """The `?since=` window, or 24 hours ago when it is absent or junk.

    A malformed timestamp falls back rather than raising: this page is
    read on a phone, and a 500 tells the reader nothing about the run.
    """
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC) - timedelta(hours=24)


def _state(remaining, latest, now):
    """One word for the top of the page.

    `done` only when nothing is outstanding. Everything else turns on
    whether the last commit is recent enough to believe work is still
    happening.
    """
    if remaining == 0:
        return "done"
    if latest is None:
        return "stalled"
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    return "running" if now - latest <= STALE_AFTER else "stalled"


@login_required
def status(request):
    since = _parse_since(request.GET.get("since"))
    now = datetime.now(UTC)

    context = {
        "since": since,
        "now": now,
        "connected": False,
        "state": "unknown",
    }

    try:
        with connections["crawler"].cursor() as cursor:
            cursor.execute(_SQL, {"since": since})
            done, remaining, latest = cursor.fetchone()
    except DatabaseError:
        # No crawler connection -- say so plainly rather than showing
        # zeroes, which read as "finished" and are the opposite.
        return render(request, "status.html", context)

    total = (done or 0) + (remaining or 0)
    context.update(
        {
            "connected": True,
            "done": done or 0,
            "remaining": remaining or 0,
            "total": total,
            # Grouped here rather than by `humanize` in the template:
            # `django.contrib.humanize` is not an installed app, and
            # `{% load %}`-ing it raises at render time, not at import.
            "done_fmt": f"{done or 0:,}",
            "remaining_fmt": f"{remaining or 0:,}",
            "total_fmt": f"{total:,}",
            "percent": round(100 * (done or 0) / total, 1) if total else 0.0,
            "latest": latest,
            "state": _state(remaining or 0, latest, now),
        }
    )
    return render(request, "status.html", context)
