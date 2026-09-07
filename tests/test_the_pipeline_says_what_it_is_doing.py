"""Nothing in Datadesk said whether the crawler was running.

The corpus dashboard counts what exists and the extraction queue lists
what needs a person; both describe the result of a run without describing
the run. Answering "is it working right now" meant opening the GCP
console (ROADMAP item 19).

Item 19 assumed live logs would come from Cloud Logging, needing
`roles/logging.viewer` in the crawler's project and billing per query.
They do not: `jobs` and `extraction_telemetry_v2` answer the same
questions, `datadesk_ro` already reads both, and on 2026-09-07 they
gained the `dataset_id` that makes a per-dataset answer a single indexed
scan rather than a join to the corpus on URL text.

The thing this page must not do is cry wolf. Production is stopped and
started deliberately and often -- every cron is suspended as this is
written -- and a page that reports a stopped pipeline as a failure is the
smoke-test problem again.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from accounts.models import DATADESK, Grant
from explorer import processing


def _user(client, role, scope=""):
    user = User.objects.create_user(role or "norole")
    if role:
        Grant.objects.create(user=user, app=DATADESK, scope=scope, role=role)
    client.force_login(user)
    return user


def _job(**kw):
    from explorer.models import Job

    fields = {
        "id": kw.pop("id", "job-1"),
        "job_type": "extraction",
        "started_at": timezone.now(),
    }
    fields.update(kw)
    return Job.objects.create(**fields)


# --- who reaches it -----------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
@pytest.mark.parametrize("role", ["viewer", None])
def test_a_viewer_does_not_reach_it(client, crawler_schema, role):
    """Production state is a management fact, the same reasoning that put
    cost on `write`. A viewer reads the corpus, not the machinery."""
    _user(client, role)
    assert client.get(reverse("explorer:processing")).status_code in (302, 403)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_editor_reaches_it(client, crawler_schema):
    _user(client, "editor")
    assert client.get(reverse("explorer:processing")).status_code == 200


# --- a stopped pipeline is not a broken one -----------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_nothing_running_reads_as_idle_not_as_failure(client, crawler_schema):
    """Every cron is suspended as this is written. The page has to say so
    without implying anything is wrong, or it is the smoke tests again."""
    _user(client, "editor")

    body = client.get(reverse("explorer:processing")).content.decode().lower()

    assert "nothing has run" in body
    for alarm in ("failed", "error", "down", "broken"):
        assert f">{alarm}<" not in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_run_that_never_reported_finishing_is_not_counted_as_running(crawler_schema):
    """A pod killed mid-batch never writes `finished_at`. Believing the row
    reports that run as live work forever."""
    _job(id="old", started_at=timezone.now() - timedelta(hours=9))
    _job(id="fresh")

    summary = processing.summarise(None)

    assert summary["running"] == 1
    assert summary["stale"] == 1


# --- counters -----------------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_job_that_never_reported_its_counts_is_not_shown_as_zero(
    client, crawler_schema
):
    """The counters were null on all 769 rows until 2026-09-07, because
    the tracker held them and never passed them to the writer. A row from
    before that processed an unknown number of records, not zero."""
    _job(id="silent", job_name="extract", records_processed=None)
    _user(client, "editor")

    body = client.get(reverse("explorer:processing")).content.decode()

    assert "—" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_summary_totals_only_what_was_reported(crawler_schema):
    _job(id="a", records_processed=10)
    _job(id="b", records_processed=None)

    assert processing.summarise(None)["processed"] == 10


# --- scoping ------------------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_no_datasets_means_no_rows_rather_than_every_row(crawler_schema):
    """The distinction the work queue got wrong: a missing dataset filter
    served every dataset's backlog. An empty selection is a selection."""
    _job(id="somebody-elses", dataset_id="ds-other")

    assert processing.jobs_for([]) == []
    assert len(processing.jobs_for(None)) == 1


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_dataset_the_caller_cannot_see_is_refused_not_widened(client, crawler_schema):
    """Refused, not quietly emptied. `@requires` decides this for every
    view in the console: a reader who picks a dataset they cannot see is
    told so, rather than shown a page that looks like the dataset has
    nothing in it. The view does not re-check -- two guards that can
    disagree are worse than one."""
    _user(client, "editor", scope="mine")

    response = client.get(reverse("explorer:processing"), {"dataset": "not-mine"})

    assert response.status_code == 403


@pytest.mark.django_db(databases=["default", "crawler"])
def test_one_dataset_offers_no_filter(client, crawler_schema):
    """A picker listing a single option is a control that cannot change
    anything."""
    from explorer.models import Dataset

    Dataset.objects.create(id="d1", slug="only", label="Only")
    _user(client, "editor", scope="only")

    response = client.get(reverse("explorer:processing"))

    assert response.context["show_filter"] is False


# --- what counts as an error --------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_filtered_url_is_not_an_error(crawler_schema):
    """A URL rejected as wire, weather or an obituary is a decision that
    worked, and a paused source is not an event. Only what the extractor
    itself reported as a failure belongs in a list read to find trouble."""
    from explorer.models import ExtractionTelemetry

    now = timezone.now()
    ExtractionTelemetry.objects.create(
        host="a.example", is_success=False, error_message=None, created_at=now
    )
    ExtractionTelemetry.objects.create(
        host="b.example", is_success=False, error_message="", created_at=now
    )
    ExtractionTelemetry.objects.create(
        host="c.example",
        is_success=False,
        error_message="connection reset",
        created_at=now,
    )

    errors = processing.recent_errors(None)

    assert [e["host"] for e in errors] == ["c.example"]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_activity_older_than_the_window_is_not_current_activity(crawler_schema):
    from explorer.models import ExtractionTelemetry

    ExtractionTelemetry.objects.create(
        host="stale.example",
        is_success=True,
        created_at=timezone.now() - processing.WINDOW - timedelta(minutes=1),
    )

    assert processing.extraction_milestones(None) == []


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_run_abandoned_weeks_ago_is_not_reported_as_live_work(crawler_schema):
    """Production holds 129 rows with no `finished_at`, every one over a
    month old and the newest from 2026-07-28. They are what a killed pod
    leaves behind, not work in flight.

    Unbounded, the page would say "129 started but never reported
    finishing" on every load, forever -- the same cry-wolf failure as
    reporting a suspended cron as broken, reached from the other side.
    """
    _job(id="abandoned", started_at=timezone.now() - timedelta(days=42))

    summary = processing.summarise(None)

    assert summary["running"] == 0
    assert summary["stale"] == 0
    assert summary["idle"] is True


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_run_stuck_for_hours_is_still_reported(crawler_schema):
    """The horizon must not swallow the case it was widened for: nine
    hours is outside the activity window and inside the abandonment one."""
    _job(id="stuck", started_at=timezone.now() - timedelta(hours=9))

    summary = processing.summarise(None)

    assert summary["stale"] == 1
    assert summary["idle"] is False
