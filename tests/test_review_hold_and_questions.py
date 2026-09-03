"""Holding a flagged record, and not asking the same question twice.

A flagged record otherwise keeps a status the pipeline reads. Everything
the queue surfaces today is already held, but every field-level defect
sits on a `labeled` article -- 85,246 of them -- which enrichment picks
up, so a flag raised there is enriched and exported before anybody looks.

Nothing releases a held record but a decision. There is no timeout: what
is a risk to the export is not knowable in advance, so the safe default is
to stop and ask, and reviews piling up is the pipeline reporting how often
it is wrong.
"""

import pytest
from django.contrib.auth.models import User

from explorer.models import Article, CandidateLink, Source
from review.dispositions import (
    ENRICHMENT,
    EXTRACTION,
    IN_REVIEW,
    LABELING,
    answered_questions,
    hold_for_review,
    question_for,
    record,
)
from review.models import ExtractionDecision


@pytest.fixture
def reviewer(db):
    return User.objects.create_user("ed", email="ed@localnewsimpact.org")


def _article(pk, crawler_schema, **kwargs):
    source, _ = Source.objects.get_or_create(
        id="s1", defaults={"host": "a.example", "host_norm": "a.example"}
    )
    link = CandidateLink.objects.create(id=f"c{pk}", source_id=source.id, url="u")
    fields = {
        "status": "labeled",
        "wire_check_status": "complete",
        "content": "A captured body.",
        "enrichment_attempts": 0,
    }
    fields.update(kwargs)
    return Article.objects.create(id=pk, candidate_link=link, **fields)


# --- the hold ----------------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_flagged_article_leaves_the_pipeline(reviewer, crawler_schema):
    """`labeled` is what enrichment selects. A defect flagged there would
    otherwise be enriched and exported before review."""
    article = _article("h1", crawler_schema, status="labeled")
    was = hold_for_review(article, LABELING)
    article.refresh_from_db()
    assert article.status == IN_REVIEW
    assert was == "labeled"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_holding_twice_does_not_lose_the_original_status(reviewer, crawler_schema):
    """The second call must not record `in_review` as what it was before."""
    article = _article("h2", crawler_schema, status="labeled")
    hold_for_review(article, LABELING)
    # Reloaded from the database, with nothing set by hand. Held on the
    # instance the prior status survived one call and was gone by the next
    # page load, which made the hold a one-way door.
    reloaded = Article.objects.get(id="h2")
    assert hold_for_review(reloaded, LABELING) == "labeled"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_answered_question_is_not_held_again(reviewer, crawler_schema):
    """Holding a record on a question somebody already answered parks it
    for a decision that has been made."""
    article = _article("h3", crawler_schema, status="obituary")
    record(article, decision=ExtractionDecision.ACCEPT, stage=EXTRACTION, user=reviewer)
    article.refresh_from_db()
    assert hold_for_review(article, EXTRACTION) == "obituary"
    article.refresh_from_db()
    assert article.status == "obituary"


# --- accept puts it back -----------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_accept_returns_a_held_article_to_what_it_was_flagged_with(
    reviewer, crawler_schema
):
    """Accept means the classification stands. Leaving the article in
    `in_review` would hold it out of the pipeline for ever on the strength
    of a decision that said nothing was wrong."""
    article = _article("h4", crawler_schema, status="obituary")
    was = hold_for_review(article, EXTRACTION)
    article.refresh_from_db()
    article.status_before_review = was

    record(
        article,
        decision=ExtractionDecision.ACCEPT,
        stage=EXTRACTION,
        user=reviewer,
        article_status=was,
    )
    article.refresh_from_db()
    assert article.status == "obituary"

    entry = ExtractionDecision.objects.get(article_id="h4")
    assert entry.status_before == "obituary"
    assert entry.question == question_for("obituary", EXTRACTION)


# --- the question, not the article --------------------------------------------


def test_the_same_claim_is_the_same_question():
    assert question_for("obituary", EXTRACTION) == question_for("obituary", EXTRACTION)


def test_a_different_claim_is_a_different_question():
    """An article whose byline is later found to be garbage is a new
    question, askable even though its classification was settled."""
    assert question_for("obituary", EXTRACTION) != question_for("wire", LABELING)


def test_the_same_claim_from_a_different_stage_is_a_different_question():
    """not_article from extraction and from the enrichment gate are two
    different claims that happen to share a status."""
    assert question_for("not_article", EXTRACTION) != question_for(
        "not_article", ENRICHMENT
    )


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_second_question_about_the_same_article_can_be_asked(
    reviewer, crawler_schema
):
    """The defect keying on the article alone would have caused: one
    decision silencing every later question."""
    article = _article("h5", crawler_schema, status="obituary")
    record(
        article,
        decision=ExtractionDecision.ACCEPT,
        stage=EXTRACTION,
        user=reviewer,
        article_status="obituary",
    )

    # Later, the same article is flagged for something else entirely.
    article.status = "wire"
    article.save(update_fields=["status"])
    record(
        article,
        decision=ExtractionDecision.REJECT,
        stage=LABELING,
        user=reviewer,
        article_status="wire",
    )

    assert ExtractionDecision.objects.filter(article_id="h5").count() == 2
    assert {e.question for e in ExtractionDecision.objects.filter(article_id="h5")} == {
        question_for("obituary", EXTRACTION),
        question_for("wire", LABELING),
    }


@pytest.mark.django_db(databases=["default", "crawler"])
def test_deciding_the_same_question_twice_replaces_the_answer(reviewer, crawler_schema):
    article = _article("h6", crawler_schema, status="obituary")
    record(
        article,
        decision=ExtractionDecision.ACCEPT,
        stage=EXTRACTION,
        user=reviewer,
        article_status="obituary",
    )
    record(
        article,
        decision=ExtractionDecision.REJECT,
        stage=EXTRACTION,
        user=reviewer,
        article_status="obituary",
    )
    assert ExtractionDecision.objects.filter(article_id="h6").count() == 1


@pytest.mark.django_db(databases=["default", "crawler"])
def test_answered_questions_reports_pairs_not_articles(reviewer, crawler_schema):
    article = _article("h7", crawler_schema, status="obituary")
    record(
        article,
        decision=ExtractionDecision.ACCEPT,
        stage=EXTRACTION,
        user=reviewer,
        article_status="obituary",
    )
    assert answered_questions(["h7"]) == {("h7", question_for("obituary", EXTRACTION))}


# --- the shape is not defined here ---------------------------------------------


def test_the_note_keys_come_from_the_shared_contract():
    """Not a local copy. Two copies, one per repository, with a test each
    that could not see the other, is what let a rename strand every held
    article."""
    from lnic_contracts import review_note as contract

    from review.dispositions import IN_REVIEW, REQUIRED_NOTE_KEYS, REVIEW_META_KEY

    assert REQUIRED_NOTE_KEYS is contract.REQUIRED_KEYS
    assert REVIEW_META_KEY == contract.METADATA_KEY
    assert IN_REVIEW == contract.IN_REVIEW


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_note_this_writes_is_readable_by_the_contract(reviewer, crawler_schema):
    """The round trip, through the definition the crawler also uses."""
    from lnic_contracts import review_note as contract

    article = _article("h8", crawler_schema, status="labeled")
    hold_for_review(article, LABELING)
    article.refresh_from_db()
    assert contract.is_readable(contract.from_metadata(article.metadata))
