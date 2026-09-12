"""Making the record agree with what a reviewer said about it.

Every rule here was wrong once, and each test names the way it was wrong,
because the failures were silent: a status nothing selects, a rewind
written to the wrong table, a re-fetch of a page that had already come
back with HTTP 200 and no text.

See MizzouNewsCrawler/docs/PIPELINE_STATES.md for what selects what.
"""

import datetime as dt

import pytest
from django.utils import timezone

from review import reconcile

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def reviewer(db):
    from django.contrib.auth.models import User

    return User.objects.create_user("ed", email="ed@localnewsimpact.org")


@pytest.fixture
def corpus(crawler_schema):
    from explorer.models import Dataset, DatasetSource, Source

    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset=dataset, source=source)
    return source


def _article(source, article_id, status, link_status="extracted", **kw):
    from explorer.models import Article, CandidateLink

    link = CandidateLink.objects.create(
        id=f"cl-{article_id}",
        url=f"https://a.example/{article_id}",
        source=source,
        status=link_status,
    )
    return Article.objects.create(
        id=article_id,
        status=status,
        candidate_link=link,
        dataset_id="d1",
        publish_date=timezone.make_aware(dt.datetime(2026, 3, 10, 12)),
        **kw,
    )


def _decide(queue, subject_type, subject_id, verb, value="", by=None):
    """A decision, with a decider. `decided_by` is NOT NULL: a decision
    nobody made is not a decision."""
    from django.contrib.auth.models import User

    from review.models import ReviewDecision

    if by is None:
        by, _ = User.objects.get_or_create(
            username="decider", defaults={"email": "decider@example.org"}
        )
    return ReviewDecision.objects.create(
        queue=queue,
        subject_type=subject_type,
        subject_id=subject_id,
        verb=verb,
        value=value,
        question=f"{queue}:{subject_id}",
        decided_by=by,
    )


# --- retraction ---------------------------------------------------------------


def test_a_story_a_reviewer_rejected_stops_being_published(corpus):
    """Articles, labels and entities all sync
    `status IN ('enriched','enrichment_skipped')`, so moving the status
    is the whole of retraction. No deletes anywhere."""
    _article(corpus, "a1", "enriched")
    _decide("extraction", "article", "a1", "reject")

    plan = reconcile.build_plan()
    change = next(c for c in plan.changes if c.pk == "a1")
    assert (change.model, change.after) == ("article", "not_article")
    assert change.after not in reconcile.PUBLISHED
    assert [c.pk for c in plan.retracted()] == ["a1"]


def test_a_url_that_was_never_a_story_takes_its_article_with_it(corpus):
    _article(corpus, "a2", "enrichment_skipped")
    _decide("discovery", "candidate_link", "cl-a2", "not_story")

    plan = reconcile.build_plan()
    assert any(c.pk == "a2" and c.after == "not_article" for c in plan.changes)


def test_a_record_already_in_the_right_state_is_not_touched(corpus):
    """217 of the 600 rejected articles are already `not_article`.
    Rewriting them would make every run look like it did work."""
    _article(corpus, "a3", "not_article")
    _decide("extraction", "article", "a3", "reject")

    assert not [c for c in reconcile.build_plan().changes if c.pk == "a3"]


# --- rewinds, and the table they belong on -------------------------------------


def test_an_accepted_article_goes_back_to_what_classification_selects(corpus):
    """`analyze` defaults to `['cleaned','local']`. An earlier draft sent
    these to `extracted`, which is a LINK status and which no stage
    selects on an article -- they would have stopped dead."""
    _article(corpus, "a4", "paused", text="A body with real words in it." * 20)
    _decide("extraction", "article", "a4", "accept")

    plan = reconcile.build_plan()
    change = next(c for c in plan.changes if c.pk == "a4")
    assert change.after == reconcile.CLASSIFIABLE == "cleaned"
    assert change.model == "article"


def test_the_fetchable_status_is_a_link_status(corpus):
    """Extraction selects `candidate_links.status='article'`. Setting an
    article to it schedules nothing at all."""
    assert reconcile.FETCHABLE_LINK == "article"


# --- what it refuses to do -----------------------------------------------------


def test_an_extraction_that_returned_no_text_is_reported_not_retried(corpus):
    """Telemetry for all 77 of these says `is_success = true`, HTTP 200,
    newspaper4k. The fetch worked and the parser produced nothing, so a
    re-queue runs the same method against the same page and fails
    identically."""
    _article(corpus, "a5", "paused", metadata={"pause_reason": "null_text"})

    plan = reconcile.build_plan()
    assert not [c for c in plan.changes if c.pk == "a5"]
    assert any(pk == "a5" and "method, not a retry" in why for pk, why in plan.skipped)


def test_an_empty_string_body_is_reported_not_retried(corpus):
    """`text = ''` is not `text IS NULL`, so housekeeping's null-text rule
    never parked these and they look like successful extractions. The 16
    a reviewer asked to re-extract are all of this shape, and their
    telemetry says `is_success = false` -- 15 having only ever tried
    `http_fetch`. They need a different method, which no status change
    expresses."""
    _article(corpus, "a6", "paused", text="")
    _decide("extraction", "article", "a6", "reextract")

    plan = reconcile.build_plan()
    assert not [c for c in plan.changes if c.pk == "a6"]
    assert any(pk == "a6" for pk, _ in plan.skipped)


def test_an_accepted_article_with_no_body_is_not_sent_to_be_classified(corpus):
    """22 of the 36 accepted articles hold no usable text. Sending them
    to `cleaned` queues an empty body for classification."""
    _article(corpus, "a7", "paused", text="   ")
    _decide("extraction", "article", "a7", "accept")

    assert not [c for c in reconcile.build_plan().changes if c.pk == "a7"]


# --- the write path ------------------------------------------------------------


def test_applying_writes_through_the_audited_path(corpus, reviewer):
    """`Article.status` and `CandidateLink.status` are both inside
    `services.WRITABLE`, so nothing here goes around the boundary."""
    from audit.models import AuditLogEntry
    from explorer.models import Article

    _article(corpus, "a8", "enriched")
    _decide("extraction", "article", "a8", "reject")

    plan = reconcile.build_plan()
    reconcile.apply_plan(plan, reviewer)

    assert Article.objects.get(id="a8").status == "not_article"
    entry = AuditLogEntry.objects.filter(action="reconcile:article").first()
    assert entry is not None
    assert entry.actor == reviewer


def test_a_run_is_idempotent(corpus, reviewer):
    """It runs nightly against a queue people are still working. A second
    run must find nothing, or every morning reports work that did not
    happen."""
    _article(corpus, "a9", "enriched")
    _decide("extraction", "article", "a9", "reject")

    reconcile.apply_plan(reconcile.build_plan(), reviewer)
    assert reconcile.build_plan().changes == []
