"""What this person has waiting, across every queue they can reach.

Somebody signing in should not have to visit three pages to find out
whether there is anything for them. The console has three review surfaces
-- publisher proposals, the extraction queue, publisher paywalls -- and
each is scoped to the datasets that person may write, so what is waiting
differs by who is asking.

COUNTED FROM THE PAGES THEMSELVES
---------------------------------
Each count comes from the same queryset its page renders, not from a
similar query written here. The extraction queue holds 1,186 rows a person
could reach by filtering and far fewer it puts in front of them, so a
to-do counting the raw status would promise work that the page does not
show -- and a number that disagrees with the page it links to is worse
than no number, because it is the one somebody plans their morning around.
"""

from dataclasses import dataclass

from accounts.privileges import WRITE
from explorer.scoping import datasets_for


@dataclass(frozen=True)
class Task:
    """One queue, in one dataset, with something in it."""

    key: str
    label: str
    count: int
    url: str
    #: What the number means, for somebody deciding whether to open it.
    note: str


def _count_proposals(dataset):
    from review.proposals import ChangeProposal

    return ChangeProposal.objects.filter(
        target="sources", state=ChangeProposal.PENDING, dataset=dataset.slug
    ).count()


def _count_extraction(dataset):
    from review import queue as review_queue

    # The queue's own selector, narrowing included, so the number is what
    # the page will show. `_apply_common` and `doubtful_q` are the same
    # ones `queued` uses; only the per-user narrowing is left off, because
    # what is IN a directory does not depend on who asks.
    qs = review_queue.base_queryset_unscoped()
    qs = review_queue._apply_common(qs, {"dataset": dataset.slug})
    # `.values("id").distinct()`, not `.distinct()`: DISTINCT over a
    # selected row fails on Postgres because `articles` carries `json`
    # columns, which have no equality operator. Only the id needs to be
    # distinct. Same reason `queued` matches on `id__in`.
    return qs.filter(review_queue.doubtful_q()).values("id").distinct().count()


def _count_paywalls(dataset):
    from explorer.models import DatasetSource, Source

    members = DatasetSource.objects.filter(dataset__slug=dataset.slug).values_list(
        "source_id", flat=True
    )
    # A publisher recorded as paywalled that nobody has priced: what the
    # paywalls page exists to collect.
    return (
        Source.objects.filter(id__in=list(members), has_paywall=True)
        .filter(subscription_cost__isnull=True)
        .count()
    )


#: Every queue this counts. Named here so the refresh and the reader agree
#: on what a directory can hold work in.
QUEUES = ("proposals", "extraction", "paywalls")

QUEUE_LABELS = {
    "proposals": "Publisher records",
    "extraction": "Articles",
    "paywalls": "Paywalls",
}

QUEUE_NOTES = {
    "proposals": "fields the scan disputes",
    "extraction": "triage flagged, worth a second look",
    "paywalls": "paywalled, no subscription recorded",
}


def count_for_dataset(dataset):
    """What is waiting in one directory, by queue.

    Counted for the directory rather than for a person: the work is the
    same whoever asks, and access decides which directories somebody is
    shown, not what is in them.

    Called by the refresh command, never from a request. One of these
    joins takes 11.2 seconds against production.
    """
    return {
        "proposals": _count_proposals(dataset),
        "extraction": _count_extraction(dataset),
        "paywalls": _count_paywalls(dataset),
    }


def for_user(user):
    """Every directory this person may write that has work in it.

    One select against the worklist table, joined in memory to the
    directories they can reach. Nothing here queries the crawler: the
    counts were taken on a schedule, because doing it per request made the
    landing page wait on nine joins over the crawler's largest tables.

    A directory with nothing waiting is left out. A to-do listing zeroes is
    a list people stop reading.
    """
    from review.models import WorklistCount

    reachable = {d.slug: d for d in datasets_for(user, WRITE)}
    if not reachable:
        return []

    counts = WorklistCount.objects.filter(dataset_slug__in=list(reachable), count__gt=0)
    by_dataset = {}
    for row in counts:
        by_dataset.setdefault(row.dataset_slug, []).append(row)

    out = []
    for slug, rows in by_dataset.items():
        dataset = reachable[slug]
        tasks = [
            Task(
                key=row.queue,
                label=QUEUE_LABELS.get(row.queue, row.queue),
                count=row.count,
                url=_url_for(row.queue, slug),
                note=QUEUE_NOTES.get(row.queue, ""),
            )
            for row in sorted(rows, key=lambda r: -r.count)
        ]
        out.append(
            {
                "dataset": dataset.label or slug,
                "slug": slug,
                "tasks": tasks,
                "total": sum(task.count for task in tasks),
                # Shown, not hidden: a number somebody plans a morning
                # around should say how old it is, and a refresh that has
                # stopped is then visible as a number that stops moving.
                "counted_at": max(row.updated_at for row in rows),
            }
        )
    return sorted(out, key=lambda row: -row["total"])


def _url_for(queue, slug):
    from django.urls import reverse

    page = {
        "proposals": "review:proposals",
        "extraction": "review:queue",
        "paywalls": "review:paywalls",
    }.get(queue)
    return f"{reverse(page)}?dataset={slug}" if page else ""


#: How long the sidebar's copy of the number is trusted, away from the
#: page that computed it. It is a count of work, not a balance: minutes
#: stale is a number somebody acts on just as well.
COUNT_CACHE_SECONDS = 300


def _cache_key(user):
    return f"review.todo.total.{user.pk}"


def remember_total(user, total):
    """Leave the number where the sidebar can read it without counting.

    Called by whoever has just counted -- the landing page -- rather than
    hidden inside a getter, so it is visible where the write happens.
    """
    import contextlib

    from django.core.cache import cache

    # A cache that cannot be written is not a reason to fail a page.
    with contextlib.suppress(Exception):
        cache.set(_cache_key(user), total, COUNT_CACHE_SECONDS)
    return total


def total_for(user):
    """Count it now, and remember it for the sidebar."""
    return remember_total(user, sum(row["total"] for row in for_user(user)))


def cached_total_for(user):
    """What the sidebar shows, or None if nothing has counted it yet.

    Never queries. A sidebar is not the place to discover the crawler
    database is unreachable, and the landing page already says so plainly.
    """
    import contextlib

    from django.core.cache import cache

    with contextlib.suppress(Exception):
        return cache.get(_cache_key(user))
    return None
