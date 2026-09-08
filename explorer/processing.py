"""What the pipeline is doing right now, read from what it recorded.

Roadmap item 19 assumed this would come from Cloud Logging, which would
need `roles/logging.viewer` in the crawler's project and would bill per
query. It does not: every question on this page is answerable from tables
Datadesk already reads through `datadesk_ro`. No new IAM, no log egress,
and the page costs what a handful of indexed queries cost.

That became true on 2026-09-07, when the telemetry tables gained a
`dataset_id` and the job rows started carrying their counters. Before
that, asking "what has extraction done for this dataset in the last hour"
meant joining 315k rows to the corpus on URL text.

**A stopped pipeline is not a broken one.** The crons are suspended
deliberately and often, and item 19 warns that a page reporting that as
failure will cry wolf the way the smoke tests did. Nothing here infers
health from absence: a stage with no recent rows is reported as idle, and
only a recorded error is reported as an error.
"""

from __future__ import annotations

from datetime import UTC, timedelta

from django.db.models import Count, Max, Q
from django.utils import timezone

from explorer.models import Article, CandidateLink, ExtractionTelemetry, Job

#: How far back "current activity" reaches. Long enough that a quiet
#: minute does not empty the page, short enough to mean "now".
WINDOW = timedelta(hours=6)

#: Rows per panel. The page refreshes every 15 seconds, so it is a window
#: on a stream rather than a report: more rows would cost more and be read
#: less.
LIMIT = 25

#: A run older than this with no finish is not running, whatever the row
#: says. A pod killed mid-batch never writes `finished_at`, and without
#: this every one of those is reported as live work forever.
STALE_AFTER = timedelta(hours=3)

#: Past this, an unfinished run is not stuck -- it is abandoned, and
#: nobody is going to act on it.
#:
#: Production holds 129 rows with no `finished_at`, every one of them more
#: than thirty days old and the newest from 2026-07-28. They are what a
#: killed pod leaves behind. Reporting them would put "129 started but
#: never reported finishing" on the page on every load, forever, which is
#: the same cry-wolf failure as reporting a suspended cron as broken.
#:
#: Wide enough to still catch the case that matters: a run stuck for nine
#: hours has aged out of the activity window and is inside this one.
ABANDONED_AFTER = timedelta(hours=24)


def _utc_naive(moment):
    """A moment on the same footing as the crawler's timestamps.

    Every timestamp on these tables is `timestamp without time zone`, and
    the crawler writes UTC into them. Django therefore hands back naive
    datetimes, and comparing one to an aware `timezone.now()` raises
    TypeError -- in production, on the first row, not in a test.
    """
    if moment is None:
        return None
    if timezone.is_aware(moment):
        return moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _now():
    return _utc_naive(timezone.now())


def _window_start():
    return _now() - WINDOW


def jobs_for(dataset_ids=None, limit=LIMIT):
    """Recent runs, newest first.

    `dataset_ids=None` means every dataset the caller may see, already
    decided by the view. An empty collection means none, and returns
    nothing rather than everything -- the distinction the work queue got
    wrong when a missing dataset filter served every dataset's backlog.
    """
    qs = Job.objects.filter(started_at__gte=_window_start())
    if dataset_ids is not None:
        qs = qs.filter(dataset_id__in=list(dataset_ids))
    return list(qs.order_by("-started_at")[:limit])


def running_jobs(dataset_ids=None):
    """Every run that has not reported finishing, at any age.

    Bounded by ABANDONED_AFTER rather than by WINDOW. A job stuck for nine
    hours has fallen out of the activity window and is the most important
    thing on the page, so WINDOW is too narrow -- but unbounded is worse:
    production's 129 unfinished rows are all over a month old, and every
    one would be reported as live work.
    """
    qs = Job.objects.filter(
        finished_at__isnull=True,
        started_at__isnull=False,
        started_at__gte=_now() - ABANDONED_AFTER,
    )
    if dataset_ids is not None:
        qs = qs.filter(dataset_id__in=list(dataset_ids))
    return list(qs.order_by("-started_at"))


def stage_counts(dataset_ids=None):
    """How many records sit at each stage, per the corpus itself.

    Not telemetry: this is the backlog, and it is true whether or not
    anything is running.
    """
    links = CandidateLink.objects.all()
    articles = Article.objects.all()
    if dataset_ids is not None:
        ids = list(dataset_ids)
        links = links.filter(dataset_id__in=ids)
        # The article's own dataset: the one it was extracted for, recorded
        # on the row from its link (MizzouNewsCrawler#540). Under the
        # deferred sharing design (docs/CROSS_DATASET_SHARING.md in the
        # crawler) a second dataset adopting the article would be a
        # membership relation; this column stays the one that did the work,
        # which is what a backlog count wants.
        articles = articles.filter(dataset_id__in=ids)

    return {
        "discovered": links.filter(status="discovered").count(),
        "article": links.filter(status="article").count(),
        "extracted": links.filter(status="extracted").count(),
        "labeled": articles.filter(status="labeled").count(),
        "enriched": articles.filter(status="enriched").count(),
    }


def extraction_milestones(dataset_ids=None, limit=LIMIT):
    """The most recent extraction attempts, as domain and outcome.

    Ordered by `created_at` against the composite index added with the
    dataset column, so this is an index scan rather than a sort of the
    whole table.
    """
    qs = ExtractionTelemetry.objects.filter(created_at__gte=_window_start())
    if dataset_ids is not None:
        qs = qs.filter(dataset_id__in=list(dataset_ids))
    return list(
        qs.order_by("-created_at").values(
            "host",
            "publisher",
            "url",
            "is_success",
            "http_status_code",
            "content_length",
            "total_duration_ms",
            "error_type",
            "error_message",
            "created_at",
        )[:limit]
    )


def recent_errors(dataset_ids=None, limit=LIMIT):
    """Only what the pipeline itself called an error.

    A URL filtered as wire, weather or an obituary is a decision that
    worked, and a paused source is not an event at all. Neither appears
    here. `is_success = false` with a recorded message is the whole of the
    filter, because that is the only thing the extractor says went wrong.
    """
    qs = (
        ExtractionTelemetry.objects.filter(
            created_at__gte=_window_start(),
            is_success=False,
        )
        .exclude(error_message__isnull=True)
        .exclude(error_message="")
    )
    if dataset_ids is not None:
        qs = qs.filter(dataset_id__in=list(dataset_ids))
    return list(
        qs.order_by("-created_at").values(
            "host",
            "url",
            "http_status_code",
            "error_type",
            "error_message",
            "created_at",
        )[:limit]
    )


def by_domain(dataset_ids=None, limit=LIMIT):
    """Extraction in the window, grouped by the domain it was against.

    The shape a person actually reads: not 400 lines of URLs but "this
    publisher is being worked, and this much of it is failing".
    """
    qs = ExtractionTelemetry.objects.filter(created_at__gte=_window_start())
    if dataset_ids is not None:
        qs = qs.filter(dataset_id__in=list(dataset_ids))
    rows = (
        qs.values("host")
        .annotate(
            attempts=Count("id"),
            succeeded=Count("id", filter=Q(is_success=True)),
            failed=Count("id", filter=Q(is_success=False)),
            latest=Max("created_at"),
        )
        .order_by("-latest")[:limit]
    )
    return list(rows)


def is_stale(job, now=None):
    """Whether a job claiming to run has simply never reported finishing."""
    if not job.is_running or job.started_at is None:
        return False
    moment = _utc_naive(now) or _now()
    return moment - _utc_naive(job.started_at) > STALE_AFTER


def summarise(dataset_ids=None):
    """One line for the top of the page.

    `running` counts only jobs that are both unfinished and recent enough
    to believe.
    """
    jobs = jobs_for(dataset_ids, limit=200)
    now = _now()
    # Unfinished runs come from their own unbounded query, so one that has
    # aged out of the activity window is still counted.
    unfinished = running_jobs(dataset_ids)
    running = [j for j in unfinished if not is_stale(j, now)]
    stale = [j for j in unfinished if is_stale(j, now)]
    failed = [j for j in jobs if (j.exit_status or "").lower() in {"failed", "error"}]

    return {
        "runs": len(jobs),
        "running": len(running),
        "stale": len(stale),
        "failed": len(failed),
        # None, not 0: a run that never reported its counters and a run
        # that processed nothing are different facts.
        "processed": sum(j.records_processed or 0 for j in jobs) or None,
        "idle": not jobs and not unfinished,
    }
