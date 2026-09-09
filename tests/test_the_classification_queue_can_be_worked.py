"""A coder reads a story and says what it is about.

The queue collects human CIN labels to measure the production model
against. Everything it does not show is as deliberate as what it does: a
label produced by somebody who has seen the model's answer cannot be
used to score the model, and every extra field on the page is a cue that
makes the judgement less independent.

See docs/CLASSIFICATION_REVIEW_QUEUE.md sections 6 and 7b.
"""

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from accounts.privileges import CLASSIFIER, REVIEWER
from review.classification import (
    CIN_LABELS,
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationDecision,
    ClassificationSample,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/review/classification/"


@pytest.fixture
def cohort(db):
    return ClassificationCohort.objects.create(number=1, slug="cohort-1")


@pytest.fixture
def coder(db, cohort):
    user = User.objects.create_user("cl", email="cl@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope=cohort.slug, role=CLASSIFIER)
    return user


@pytest.fixture
def story(crawler_schema, cohort):
    """One assigned article with a real body."""
    from explorer.models import Article, CandidateLink, Source

    source = Source.objects.using("crawler").create(
        id="s1", host="a.example", canonical_name="A Paper"
    )
    link = CandidateLink.objects.using("crawler").create(
        id="cl-1", url="https://a.example/one", source=source, status="extracted"
    )
    Article.objects.using("crawler").create(
        id="a-1",
        candidate_link=link,
        url="https://a.example/one",
        title="Council votes on the new library",
        status="labeled",
        text=" ".join(f"word{n}" for n in range(600)),
        primary_label="Civic Life",
        primary_label_confidence=0.91,
    )
    ClassificationSample.objects.create(
        cohort=cohort,
        article_id="a-1",
        stratum=ClassificationSample.CONFIDENT,
        inclusion_probability=0.25,
    )
    return link


@pytest.fixture
def assigned(cohort, coder, story):
    return ClassificationAssignment.objects.create(
        cohort=cohort, article_id="a-1", assigned_to=coder
    )


# ------------------------------------------------------- what is shown


def test_the_headline_and_body_are_shown(client, coder, assigned):
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert "Council votes on the new library" in body
    assert "word0" in body


def test_the_url_is_text_and_not_a_link(client, coder, assigned):
    """A coder who opens the publisher's page is reading the live
    article -- including whatever it has become since capture -- and is
    no longer labelling the row in front of them."""
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert "https://a.example/one" in body
    assert 'href="https://a.example/one"' not in body


def test_the_body_is_cut_to_about_what_the_model_reads(client, coder, assigned):
    """The model sees title + body truncated at 512 BERT tokens, roughly
    350-400 words. A window much larger means some disagreement measures
    the truncation rather than the model."""
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert "word249" in body
    assert "word400" not in body


def test_the_models_own_answer_is_never_shown(client, coder, assigned):
    """The whole point. A label produced after seeing the model's answer
    cannot score the model.

    The article carries `Civic Life` at 0.91 confidence and neither may
    appear -- nor the status, which is another route to the same cue.
    """
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert "0.91" not in body
    # `Civic Life` is a legitimate dropdown option; what must not appear
    # is the article's own label presented as one.
    assert "labeled" not in body
    assert "primary_label" not in body


def test_both_dropdowns_offer_the_ten(client, coder, assigned):
    client.force_login(coder)
    body = client.get(URL).content.decode()
    for label in CIN_LABELS:
        assert label in body
    assert 'name="primary"' in body
    assert 'name="secondary"' in body


def test_the_reject_reasons_are_offered_apart_from_the_categories(
    client, coder, assigned
):
    """A rejection is not a label. Storing one as a category is what made
    the historical primary column unusable without filtering."""
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert 'name="reject"' in body
    assert ClassificationDecision.NOT_LOCAL in body


# --------------------------------------------------------- what it does


def test_answering_records_the_label_and_closes_the_assignment(client, coder, assigned):
    client.force_login(coder)
    client.post(URL, {"assignment": assigned.pk, "primary": "Civic Life"})
    decision = ClassificationDecision.objects.get()
    assert decision.primary_label == "Civic Life"
    assert decision.decided_by == coder
    assigned.refresh_from_db()
    assert assigned.completed_at is not None


def test_the_draw_travels_onto_the_decision(client, coder, assigned):
    """So a rate can be weighted without joining back to a sample that
    may have been superseded."""
    client.force_login(coder)
    client.post(URL, {"assignment": assigned.pk, "primary": "Sports"})
    decision = ClassificationDecision.objects.get()
    assert decision.stratum == ClassificationSample.CONFIDENT
    assert decision.inclusion_probability == 0.25


def test_a_rejection_stores_no_label(client, coder, assigned):
    client.force_login(coder)
    client.post(
        URL,
        {
            "assignment": assigned.pk,
            "primary": "Sports",
            "reject": ClassificationDecision.TOO_SHORT,
        },
    )
    decision = ClassificationDecision.objects.get()
    assert decision.is_rejection
    assert decision.primary_label == ""
    assert decision.secondary_label == ""


def test_an_empty_answer_is_refused(client, coder, assigned):
    client.force_login(coder)
    client.post(URL, {"assignment": assigned.pk})
    assert not ClassificationDecision.objects.exists()
    assigned.refresh_from_db()
    assert assigned.completed_at is None


def test_a_coder_cannot_answer_somebody_elses_assignment(
    client, coder, cohort, story, db
):
    """The assignment is the whole of a classifier's access."""
    other = User.objects.create_user("other", email="o@localnewsimpact.org")
    theirs = ClassificationAssignment.objects.create(
        cohort=cohort, article_id="a-1", assigned_to=other
    )
    client.force_login(coder)
    client.post(URL, {"assignment": theirs.pk, "primary": "Sports"})
    assert not ClassificationDecision.objects.exists()
    theirs.refresh_from_db()
    assert theirs.completed_at is None


def test_nothing_assigned_says_so(client, coder):
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert "Nothing assigned to you" in body


# ------------------------------------------------------------- access


def test_a_reviewer_cannot_open_it(client, db):
    """`classify` is not on the ladder, so holding `write` does not carry
    it. Only editors, admins and classifiers may label."""
    user = User.objects.create_user("rv", email="rv@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role=REVIEWER)
    client.force_login(user)
    assert client.get(URL).status_code == 403


def test_the_labels_come_from_the_contract():
    """Not restated. I wrote them out here first and used a different
    order from the crawler's without noticing, which is exactly the
    drift the contract exists to prevent."""
    from lnic_contracts import cin_labels

    assert CIN_LABELS == cin_labels.CODEBOOK_ORDER
    # The model's order is a different tuple and is not what a coder
    # reads: a dropdown is a list for a person, not a class-id map.
    assert CIN_LABELS != cin_labels.LABELS
