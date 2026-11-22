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


def test_the_coder_sees_the_headline_and_text_and_nothing_else(client, coder, assigned):
    """What the V1 coders were given, so what a V2 answer must be given
    to be compared with theirs: no URL, publisher, date or model label.
    A URL names the publisher and often the section, which is the
    answer."""
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert "Council votes on the new library" in body
    assert "word0" in body
    assert "a.example" not in body
    assert "A Paper" not in body


def test_a_story_with_no_cleaned_text_shows_no_page_capture(
    client, coder, assigned, crawler_schema
):
    """The raw capture is the whole page -- dates, captions, menus. A
    story without cleaned text is shown with none, and the coder rejects
    it as a technical error."""
    from explorer.models import Article

    Article.objects.using("crawler").filter(id="a-1").update(
        text="", raw="Posted Nov 3 PHOTO CAPTION menu"
    )
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert "PHOTO CAPTION" not in body
    assert "Council votes on the new library" in body


def test_the_coder_reads_the_whole_story(client, coder, assigned):
    """As the V1 coders did. The pilot compares V2 answers with V1
    answers on the same stories, and an answer given on the first 250
    words is not an answer on the same story."""
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert "word0" in body
    assert "word599" in body
    assert "about what the" not in body


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


def test_an_admin_who_is_given_stories_sees_them(client, db, cohort, story):
    """An admin's scopes are "everything", not a list of batches. The
    queue read that as a list, matched no batch, and showed an admin who
    had been added to a round an empty page."""
    for name, flags in (("root", {"is_superuser": True}), ("lead", {})):
        user = User.objects.create_user(name, email=f"{name}@x.org", **flags)
        if not flags:
            # An application-wide grant: every scope, as a superuser has.
            from accounts.privileges import ADMIN

            Grant.objects.create(user=user, app=DATADESK, scope="", role=ADMIN)
        ClassificationAssignment.objects.create(
            cohort=cohort, article_id="a-1", assigned_to=user
        )
        client.force_login(user)
        body = client.get(URL).content.decode()
        assert "Council votes on the new library" in body, name
        response = client.post(
            URL,
            {
                "assignment": ClassificationAssignment.objects.get(assigned_to=user).pk,
                "primary": "Political life",
            },
        )
        assert response.status_code in (200, 302), name
        assert ClassificationDecision.objects.filter(decided_by=user).count() == 1


def test_a_story_stored_as_one_line_is_shown_in_paragraphs(client, coder, assigned):
    """Four in ten stories are stored with no line breaks, and came up
    as one block the width of the page. Leftover markup came up as
    markup."""
    from explorer.models import Article
    from review.classification import readable_text

    text = (
        "The council met Monday. It voted 5-2 to fund the library. "
        "Mayor De&rsquo;Carlon Seewood signed it. "
        'The copy seen by <a href="http://example.com">Example</a> is unsigned. '
        "“We are pleased,” he said. A second vote is due in March. Then it ends."
    )
    Article.objects.using("crawler").filter(id="a-1").update(text=text)
    client.force_login(coder)
    body = client.get(URL).content.decode()
    story = body.split('class="story-body"')[1].split("</div>")[0]
    # Seven sentences, three to a paragraph.
    assert story.count("<p>") == 3
    assert "De’Carlon Seewood" in body and "rsquo" not in body
    assert "seen by Example is unsigned" in body and "example.com" not in body

    # Nothing is added, dropped or reordered: the same words, in order.
    plain = "The council met. It voted. The mayor signed. A vote is due. It ends."
    assert readable_text(plain).split() == plain.split()
    assert readable_text(plain).count("\n\n") == 1
    # Text stored with its own breaks keeps them, and only them.
    assert readable_text("One. Two. Three. Four.\n\nFive.") == (
        "One. Two. Three. Four.\n\nFive."
    )
    assert readable_text("") == "" and readable_text(None) == ""


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


def test_the_form_sits_beside_the_story(client, coder, assigned):
    """The instructions, the count and the form are in a column beside
    the story, after it in the page, rather than below a scrolling
    block."""
    client.force_login(coder)
    body = client.get(URL).content.decode()
    layout = body.split('class="classify-layout"')[1]
    story, panel = layout.split('class="classify-panel"')
    assert 'class="story-body"' in story
    assert "left in this batch" in panel
    assert 'name="primary"' in panel and "Save and next" in panel
    # The ten categories are in a box under the button, not behind a link.
    after_button = panel.split("Save and next")[1]
    assert 'class="category-box"' in after_button
    assert after_button.count("<dt>") == 10
    assert 'class="instructions-open"' not in body


def test_a_story_answered_in_another_batch_is_not_offered_again(client, coder):
    """Two batches drawn at once could share a story, and saving it a
    second time was a server error."""
    from review.classification import (
        ClassificationAssignment,
        ClassificationCohort,
        ClassificationDecision,
        ClassificationSample,
    )

    first = ClassificationCohort.objects.create(number=21, slug="cohort-21")
    second = ClassificationCohort.objects.create(number=22, slug="cohort-22")
    for cohort in (first, second):
        ClassificationSample.objects.create(
            cohort=cohort, article_id="a-1", stratum="random"
        )
        Grant.objects.create(
            user=coder, app=DATADESK, scope=cohort.slug, role=CLASSIFIER
        )
    one = ClassificationAssignment.objects.create(
        cohort=first, article_id="a-1", assigned_to=coder
    )
    two = ClassificationAssignment.objects.create(
        cohort=second, article_id="a-1", assigned_to=coder
    )
    client.force_login(coder)
    client.post(URL, {"assignment": one.pk, "primary": "Political life"})
    assert ClassificationDecision.objects.filter(decided_by=coder).count() == 1

    # Not offered again ...
    body = client.get(URL).content.decode()
    assert f'name="assignment" value="{two.pk}"' not in body
    # ... and a stale form for it closes the duplicate instead of failing.
    response = client.post(URL, {"assignment": two.pk, "primary": "Sports"})
    assert response.status_code == 302
    two.refresh_from_db()
    assert two.completed_at is not None
    decision = ClassificationDecision.objects.get(decided_by=coder)
    assert decision.primary_label == "Political life"
