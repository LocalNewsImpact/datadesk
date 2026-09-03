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


def _proposal_tasks(user, dataset):
    from django.urls import reverse

    from review.proposals import ChangeProposal
    from review.views import _within_reach

    pending = _within_reach(
        ChangeProposal.objects.filter(
            target="sources", state=ChangeProposal.PENDING, dataset=dataset.slug
        ),
        user,
    ).count()
    if not pending:
        return []
    return [
        Task(
            key="proposals",
            label="Publisher records",
            count=pending,
            url=f"{reverse('review:proposals')}?dataset={dataset.slug}",
            note="fields the scan disputes",
        )
    ]


def _extraction_tasks(user, dataset):
    from django.urls import reverse

    from review import queue as review_queue

    # The queue's own selector, narrowing included, so the number is what
    # the page will show.
    waiting = review_queue.queued({"dataset": dataset.slug}, user).count()
    if not waiting:
        return []
    return [
        Task(
            key="extraction",
            label="Articles",
            count=waiting,
            url=f"{reverse('review:queue')}?dataset={dataset.slug}",
            note="triage flagged, worth a second look",
        )
    ]


def _paywall_tasks(user, dataset):
    from django.urls import reverse

    from explorer.models import DatasetSource, Source
    from explorer.scoping import narrow

    reachable = narrow(Source.objects.all(), user, WRITE, source_path="id")
    members = DatasetSource.objects.filter(dataset__slug=dataset.slug).values_list(
        "source_id", flat=True
    )
    # A publisher recorded as paywalled that nobody has priced or signed
    # into: the two things the paywalls page exists to collect.
    undecided = (
        reachable.filter(id__in=list(members), has_paywall=True)
        .filter(subscription_cost__isnull=True)
        .count()
    )
    if not undecided:
        return []
    return [
        Task(
            key="paywalls",
            label="Paywalls",
            count=undecided,
            url=f"{reverse('review:paywalls')}?dataset={dataset.slug}",
            note="paywalled, no subscription recorded",
        )
    ]


def for_user(user):
    """Every dataset with work in it, and what kind.

    Datasets with nothing waiting are left out. A to-do listing zeroes is a
    list somebody stops reading.
    """
    out = []
    for dataset in datasets_for(user, WRITE):
        tasks = (
            _proposal_tasks(user, dataset)
            + _extraction_tasks(user, dataset)
            + _paywall_tasks(user, dataset)
        )
        if tasks:
            out.append(
                {
                    "dataset": dataset.label or dataset.slug,
                    "slug": dataset.slug,
                    "tasks": tasks,
                    "total": sum(task.count for task in tasks),
                }
            )
    return sorted(out, key=lambda row: -row["total"])


#: How long the sidebar's copy of the number is trusted. It is a count of
#: work, not a balance: five minutes stale is a number somebody acts on
#: just as well, and it is refreshed every time the landing page is drawn.
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
