"""Acting on a classification, and what each decision actually writes.

Reject is a status rewind, not a re-extraction. The pipeline advances by
status -- labeling promotes only from `cleaned`, enrichment selects
`labeled` -- so an article removed at some stage is returned by putting
its status back to the stage before the one that erred. It has already
been extracted; fetching the URL again would produce the same result from
the same page.
"""

import pytest
from django.contrib.auth.models import User

from explorer.models import Article, CandidateLink, Source
from review.dispositions import (
    ENRICHMENT,
    EXTRACTION,
    LABELING,
    can_reextract,
    has_body_to_rewind_to,
    record,
    rewind_target,
    stage_of,
    verbs_for,
)
from review.models import ReviewDecision


@pytest.fixture
def reviewer(db):
    return User.objects.create_user("ed", email="ed@localnewsimpact.org")


@pytest.fixture
def article(crawler_schema):
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    link = CandidateLink.objects.create(id="c1", source_id=source.id, url="u")
    return Article.objects.create(
        id="a1",
        candidate_link=link,
        status="not_article",
        title="A real story",
        content="A body that was captured.",
        text="",
        wire_check_status="complete",
    )


# --- which stage made the claim ---------------------------------------------


def test_an_enrichment_row_means_the_gate_decided_it():
    assert stage_of(None, enrichment=object()) == ENRICHMENT


def test_labels_without_an_enrichment_row_mean_labeling_decided_it():
    assert stage_of(None, labels_updated_at="2026-01-01") == LABELING


def test_neither_means_extraction_decided_it():
    assert stage_of(None) == EXTRACTION


# --- where reject rewinds to -------------------------------------------------


@pytest.mark.parametrize(
    "stage,target",
    [(EXTRACTION, "cleaned"), (LABELING, "cleaned"), (ENRICHMENT, "labeled")],
)
def test_reject_rewinds_to_the_stage_before_the_error(stage, target):
    """Labeling promotes from `cleaned`; enrichment reads `labeled`."""
    assert rewind_target(stage) == target


def test_an_unknown_stage_rewinds_nowhere():
    assert rewind_target("something else") is None


# --- what each decision writes -----------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_reject_writes_the_status_and_nothing_else(reviewer, article):
    record(
        article,
        decision="reject",
        stage=ENRICHMENT,
        user=reviewer,
        reason="a real story",
    )
    article.refresh_from_db()
    assert article.status == "labeled"
    entry = ReviewDecision.objects.get(subject_id="a1")
    assert entry.wrote.get("rewound_to", "") == "labeled"
    assert entry.claim == "not_article"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_accept_writes_nothing_to_the_article(reviewer, article):
    """Its status already excludes it -- enrichment selects `labeled` and
    nothing else. The only thing needed is that the queue stops asking."""
    record(article, decision="accept", stage=EXTRACTION, user=reviewer)
    article.refresh_from_db()
    assert article.status == "not_article"
    assert ReviewDecision.objects.get(subject_id="a1").wrote.get("rewound_to", "") == ""


@pytest.mark.django_db(databases=["default", "crawler"])
def test_answering_the_same_question_twice_replaces_the_answer(reviewer, article):
    """Same claim, same stage, so the same question -- the later decision
    stands in place of the earlier one."""
    record(article, decision="accept", stage=ENRICHMENT, user=reviewer)
    record(article, decision="reject", stage=ENRICHMENT, user=reviewer)
    assert ReviewDecision.objects.filter(subject_id="a1").count() == 1
    assert ReviewDecision.objects.get(subject_id="a1").verb == "reject"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_decision_at_another_stage_is_another_question(reviewer, article):
    """not_article from extraction and from the enrichment gate are two
    different claims that happen to share a status, so both are asked and
    both are answered."""
    record(article, decision="accept", stage=EXTRACTION, user=reviewer)
    record(article, decision="reject", stage=ENRICHMENT, user=reviewer)
    assert ReviewDecision.objects.filter(subject_id="a1").count() == 2


# --- which verbs a row can actually carry out --------------------------------


def test_a_row_with_a_body_can_be_rejected():
    class Row:
        content, text, raw_gcs_path = "a body", "", ""

    assert "reject" in verbs_for(Row(), EXTRACTION)


def test_a_row_with_no_body_is_offered_re_extraction_instead():
    """Extraction dropped `content` as well on 788 rows. Rewinding one
    sends an empty body to the labeler."""

    class Row:
        content, text, raw_gcs_path = "", "", "gs://bucket/page.html.gz"

    verbs = verbs_for(Row(), EXTRACTION)
    assert "reextract" in verbs and "reject" not in verbs


def test_a_row_with_no_body_and_no_archive_can_only_be_accepted():
    """The bucket keeps 30 days. After that there is nothing to re-parse."""

    class Row:
        content, text, raw_gcs_path = "", "", ""

    assert verbs_for(Row(), EXTRACTION) == ["accept"]


@pytest.mark.parametrize(
    "content,text,expected",
    [("body", "", True), ("", "body", True), ("", "", False), ("   ", "  ", False)],
)
def test_what_counts_as_a_body(content, text, expected):
    class Row:
        pass

    row = Row()
    row.content, row.text = content, text
    assert has_body_to_rewind_to(row) is expected


def test_re_extraction_needs_an_archived_page():
    class Row:
        raw_gcs_path = ""

    assert can_reextract(Row()) is False


# --- re-extraction is a rewind, not a request --------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_re_extraction_takes_the_article_out_of_the_pipeline(reviewer, crawler_schema):
    """One step further back than a rejection: the body has to be rebuilt
    before anything can read it, so the status goes to `extracted`.

    There is no separate request and no worker waiting on one. A decision
    recorded somewhere nothing reads is a decision that does not happen.
    """
    source = Source.objects.create(id="s2", host="b.example", host_norm="b.example")
    link = CandidateLink.objects.create(id="c2", source_id=source.id, url="u")
    article = Article.objects.create(
        id="a2",
        candidate_link=link,
        status="not_article",
        title="Body was dropped",
        content="",
        text="",
        raw_gcs_path="gs://bucket/page.html.gz",
        wire_check_status="complete",
    )

    record(article, decision="reextract", stage=EXTRACTION, user=reviewer)

    article.refresh_from_db()
    # Not `extracted`: that asserts extraction succeeded, queues a
    # body-less row for labelling, and is the exact shape housekeeping
    # pauses -- so the decision would be undone by the next run.
    assert article.status == "paused"
    decision = ReviewDecision.objects.get(subject_id="a2")
    assert decision.wrote.get("rewound_to", "") == "paused"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_archived_capture_is_the_flag(reviewer, crawler_schema):
    """`raw_gcs_path` is non-null exactly when the page is in the archive,
    so nothing new has to be written to say a body can be rebuilt."""
    source = Source.objects.create(id="s3", host="c.example", host_norm="c.example")
    link = CandidateLink.objects.create(id="c3", source_id=source.id, url="u")
    with_archive = Article.objects.create(
        id="a3",
        candidate_link=link,
        status="not_article",
        content="",
        text="",
        raw_gcs_path="gs://bucket/page.html.gz",
        wire_check_status="complete",
    )
    assert can_reextract(with_archive)

    link2 = CandidateLink.objects.create(id="c4", source_id=source.id, url="u")
    without = Article.objects.create(
        id="a4",
        candidate_link=link2,
        status="not_article",
        content="",
        text="",
        wire_check_status="complete",
    )
    assert not can_reextract(without)
