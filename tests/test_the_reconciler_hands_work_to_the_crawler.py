"""A rewound record owes the crawler work, and the reconciler says so in
the table the crawler reads.

Changing a status is not enough. Every crawler stage selects by status,
and a rewound record shares its status with the whole backlog -- so the
nightly housekeeping run cannot tell "the 14 a reviewer sent back" from
"the 450 sitting at `cleaned`", and the first time it tried it took 4,802
records nobody had asked about. `pipeline_rework` is the list of what
was asked. The reconciler writes it; a stage reads only it.

Steps 6 and 7 of MizzouNewsCrawler/docs/HOUSEKEEPING_PLAN.md.
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


def _link(source, link_id, status):
    from explorer.models import CandidateLink

    return CandidateLink.objects.create(
        id=link_id, url=f"https://a.example/{link_id}", source=source, status=status
    )


def _article(source, article_id, status, link_status="extracted", **kw):
    from explorer.models import Article

    link = _link(source, f"cl-{article_id}", link_status)
    return Article.objects.create(
        id=article_id,
        status=status,
        candidate_link=link,
        dataset_id="d1",
        publish_date=timezone.make_aware(dt.datetime(2026, 3, 10, 12)),
        **kw,
    )


def _decide(queue, subject_type, subject_id, verb, value="", question=None):
    from django.contrib.auth.models import User

    from review.models import ReviewDecision

    by, _ = User.objects.get_or_create(
        username="decider", defaults={"email": "decider@example.org"}
    )
    return ReviewDecision.objects.create(
        queue=queue,
        subject_type=subject_type,
        subject_id=subject_id,
        verb=verb,
        value=value,
        question=question or f"{queue}:{subject_id}",
        decided_by=by,
    )


def _owed():
    from explorer.models import PipelineRework

    return sorted(
        (r.record_type, r.record_id, r.stage)
        for r in PipelineRework.objects.filter(done_at__isnull=True)
    )


BODY = "A body with real words in it. " * 20
REJECTED = reconcile.REJECTED


# --- step 6: a rewind produces a row naming the stage --------------------------


def test_an_accepted_article_owes_a_classification(corpus):
    """Back to `cleaned` is what classification selects; the row is what
    makes housekeeping's classifier take THIS one and not the 450."""
    _article(corpus, "a1", "paused", text=BODY)
    _decide("extraction", "article", "a1", "accept")

    plan = reconcile.build_plan()

    assert plan.rework == [
        reconcile.Rework(
            "article", "a1", "classify", "review: accept, back to classification"
        )
    ]


def test_a_retraction_owes_nothing(corpus):
    """`not_article` is terminal. No stage selects it and none should."""
    _article(corpus, "a2", "enriched")
    _decide("extraction", "article", "a2", "reject")

    plan = reconcile.build_plan()

    assert [c.after for c in plan.changes] == ["not_article"]
    assert plan.rework == []


def test_a_restored_link_goes_back_to_verification_which_is_not_housekeepings(
    corpus,
):
    """`discovery_verdict.link_status_for` sends a restored story to
    `discovered`, which is URL verification's input -- and housekeeping
    starts at records ready for extraction, not at discovery. So the
    status moves and no row is written: a row for a stage no workflow
    runs would sit open forever and make the nightly guard fire on
    nothing."""
    _link(corpus, "l3", "not_article")
    _decide("discovery", "candidate_link", "l3", "story", value="news")

    plan = reconcile.build_plan()

    assert [(c.pk, c.after) for c in plan.changes] == [("l3", "discovered")]
    assert plan.rework == []


def test_a_restored_link_whose_article_has_a_body_sends_the_article(corpus):
    """The link's own move is verification's business. The article is
    housekeeping's: a superseded "not a story" decision retracted it on
    an earlier night, latest-wins stops the retraction rule firing but
    does not undo the write, and nothing else puts it back. It has a
    body, so `cleaned` is where it re-enters."""
    _article(corpus, "a4", REJECTED, link_status="not_article", text=BODY)
    _decide("discovery", "candidate_link", "cl-a4", "story", value="news")

    plan = reconcile.build_plan()

    assert plan.rework == [
        reconcile.Rework(
            "article", "a4", "classify", "discovery: restored, back to classification"
        )
    ]
    article = next(c for c in plan.changes if c.pk == "a4")
    assert article.after == "cleaned"


def test_a_restored_link_whose_article_has_no_body_is_refused(corpus):
    """Nothing to classify and nothing re-fetches it. A method, not a
    retry -- reported, so it is not silently nothing."""
    _article(corpus, "a5", REJECTED, link_status="not_article", text="")
    _decide("discovery", "candidate_link", "cl-a5", "story", value="news")

    plan = reconcile.build_plan()

    assert plan.rework == []
    assert any(pk == "cl-a5" for pk, _ in plan.skipped)


def test_a_withheld_kind_owes_nothing(corpus):
    """Opinion is a status no stage selects. The record stops; no row."""
    _article(corpus, "a6", "enriched")
    _decide("discovery", "candidate_link", "cl-a6", "story", value="opinion")

    plan = reconcile.build_plan()

    assert plan.rework == []


# --- step 7: by record, and conflicts are refused --------------------------------


def test_the_latest_decision_on_a_subject_is_the_one_that_counts(corpus):
    """A person who said "not a story" and then "story" changed their
    mind, and the second is the verdict.

    `one_review_decision_per_question` means re-answering the SAME
    question replaces the row, so a change of mind there needs nothing
    from this module. Two rows for one subject are what the constraint
    permits: a different question -- the queue asked again about a
    different claim, or in a different stage. Reading both makes two
    rules disagree about a record the reviewer was clear on, so the
    latest row is the verdict and the verb filter comes after it.
    """
    _link(corpus, "l7", "not_article")
    _decide("discovery", "candidate_link", "l7", "not_story", question="first ask")
    _decide(
        "discovery", "candidate_link", "l7", "story", value="news", question="again"
    )

    plan = reconcile.build_plan()

    assert [(c.pk, c.after) for c in plan.changes] == [("l7", "discovered")]


def test_two_rules_that_disagree_about_a_record_are_both_refused(corpus):
    """Discovery said the URL was never a story; extraction accepted its
    article. `not_article` and `cleaned` cannot both be right, and the
    last rule to run must not win by being last. Neither is applied and
    the record is reported."""
    _article(corpus, "a8", "paused", text=BODY)
    _decide("discovery", "candidate_link", "cl-a8", "not_story")
    _decide("extraction", "article", "a8", "accept")

    plan = reconcile.build_plan()

    assert [c for c in plan.changes if c.pk == "a8"] == []
    assert plan.rework == []
    why = dict(plan.skipped)["a8"]
    assert "not_article" in why and "cleaned" in why


def test_two_rules_that_agree_count_the_record_once(corpus):
    """Two articles were double-counted the first night: two rules, same
    answer, two changes. A record moves once."""
    _article(corpus, "a9", "enriched")
    _decide("discovery", "candidate_link", "cl-a9", "not_story")
    _decide("extraction", "article", "a9", "reject")

    plan = reconcile.build_plan()

    assert len([c for c in plan.changes if c.pk == "a9"]) == 1
    assert sum(plan.by_rule().values()) == 1


# --- applying writes the rows, once ---------------------------------------------


def test_applying_writes_the_rows_through_the_audited_boundary(corpus, reviewer):
    from audit.models import AuditLogEntry
    from explorer.models import PipelineRework

    _article(corpus, "a10", "paused", text=BODY)
    _decide("extraction", "article", "a10", "accept")

    reconcile.apply_plan(reconcile.build_plan(), reviewer)

    row = PipelineRework.objects.get(record_id="a10")
    assert (row.record_type, row.stage, row.done_at) == ("article", "classify", None)
    assert row.requested_by == reviewer.username
    assert row.reason == "review: accept, back to classification"
    entry = AuditLogEntry.objects.get(action="reconcile:rework")
    assert entry.target_table == "pipeline_rework"
    assert entry.actor == reviewer


def test_a_second_run_does_not_ask_twice(corpus, reviewer):
    """The status is already `cleaned`, so there is no change and no
    row. And if the crawler has not yet taken the row, it is still
    outstanding: asking again is the same ask, not a second one."""
    _article(corpus, "a11", "paused", text=BODY)
    _decide("extraction", "article", "a11", "accept")

    reconcile.apply_plan(reconcile.build_plan(), reviewer)
    reconcile.apply_plan(reconcile.build_plan(), reviewer)

    assert _owed() == [("article", "a11", "classify")]


def test_a_row_the_crawler_settled_is_not_reopened_by_the_same_decision(
    corpus, reviewer
):
    """Housekeeping classified it (`labeled`, row closed). The decision
    that sent it back is still in the queue's history; it has been
    carried out and must not send it back again."""
    from explorer.models import Article, PipelineRework

    _article(corpus, "a12", "paused", text=BODY)
    _decide("extraction", "article", "a12", "accept")
    reconcile.apply_plan(reconcile.build_plan(), reviewer)
    PipelineRework.objects.filter(record_id="a12").update(
        done_at=timezone.now(), outcome="classified"
    )
    Article.objects.filter(id="a12").update(status="labeled")

    plan = reconcile.build_plan()

    assert plan.changes == [] and plan.rework == []
    assert _owed() == []


def test_a_row_is_written_only_for_a_change_that_was_applied(corpus, reviewer):
    """A refused record gets no row: the row is the instruction and a
    refusal is the absence of one."""
    _article(corpus, "a13", REJECTED, link_status="not_article", text="")
    _decide("discovery", "candidate_link", "cl-a13", "story", value="news")

    reconcile.apply_plan(reconcile.build_plan(), reviewer)

    assert _owed() == []


def test_the_rework_table_is_creatable_and_nothing_else(corpus):
    """Through `audited_create` only. Nothing updates or deletes a row
    from this side: the crawler closes them, and closed is the record."""
    from explorer.models import PipelineRework
    from review import services

    assert PipelineRework in services.CREATABLE
    assert PipelineRework not in services.WRITABLE
    assert PipelineRework not in services.DELETABLE


# --- what the nightly run says it did ------------------------------------------


def test_the_command_reports_what_owes_a_stage(corpus, reviewer):
    """ "Did the run schedule anything" is a different question from "did
    it change anything", and only the first one predicts whether tonight's
    housekeeping does any work."""
    from io import StringIO

    from django.core.management import call_command

    _article(corpus, "a14", "paused", text=BODY)
    _decide("extraction", "article", "a14", "accept")

    out = StringIO()
    call_command("reconcile_queues", stdout=out)
    report = out.getvalue()

    assert "1 records owe a crawler stage:" in report
    assert "classify" in report
    assert "Nothing written" in report


def test_a_report_only_run_writes_no_rows(corpus):
    from django.core.management import call_command

    from explorer.models import PipelineRework

    _article(corpus, "a15", "paused", text=BODY)
    _decide("extraction", "article", "a15", "accept")

    call_command("reconcile_queues")

    assert not PipelineRework.objects.exists()
