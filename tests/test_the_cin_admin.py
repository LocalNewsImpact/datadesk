"""The work about the classification queue, as distinct from the queue.

Three pages: whether the coders agree, who is doing it, and what they
are told to do. The last of those is the reason this exists -- the
codebook spent its life as a PDF attachment, and changing a word of it
meant a pull request and a deploy.

The definitions are known to be imperfect. Agreement across the original
cohorts was 64.6% exact and Civic information behaves as a catch-all, so
the guidance WILL be rewritten, and the point of rewriting it is to
measure whether agreement moves. A day between a decision and its effect
made that loop useless.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from accounts.privileges import ADMIN, CLASSIFIER, EDITOR
from review.classification import (
    CIN_LABELS,
    CODING_GUIDE,
    DEFAULT_INSTRUCTIONS,
    ClassificationCohort,
    ClassificationDecision,
    CodebookSettings,
    agreement_report,
    guidance,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def _user(username, role, scope=""):
    user = User.objects.create_user(username, password="x")
    Grant.objects.create(user=user, app=DATADESK, role=role, scope=scope)
    return user


# ------------------------------------------------------- the guidance holds


def test_every_category_has_guidance():
    """A category in the menu with nothing said about it is a category
    coded by guesswork. This caught two on its first run: the vocabulary
    spells them `Civic information` and `Political life`, and the
    codebook's worked-examples table spells both with capitals."""
    missing = [label for label in CIN_LABELS if label not in CODING_GUIDE]
    assert not missing, missing


def test_the_guidance_invents_no_categories():
    """The reverse: guidance for something not in the vocabulary would
    show a coder a category they cannot choose."""
    extra = [label for label in CODING_GUIDE if label not in CIN_LABELS]
    assert not extra, extra


def test_guidance_follows_the_menu_order():
    """The panel and the menu cannot disagree about what comes first."""
    assert [row["label"] for row in guidance()] == list(CIN_LABELS)


def test_every_category_carries_an_action_test():
    """The action test is the instrument the codebook gives for choosing
    between two plausible categories. A row without one is a definition
    with no way to apply it."""
    for row in guidance():
        assert row["reader_could"], row["label"]
        assert row["covers"], row["label"]


# ------------------------------------------------------ the instructions


def test_a_fresh_database_ships_with_its_specification():
    """An empty instructions panel is a queue shipped without the
    codebook it implements."""
    assert CodebookSettings.objects.count() == 0
    row = CodebookSettings.load()
    assert row.instructions == DEFAULT_INSTRUCTIONS
    assert CodebookSettings.load().pk == row.pk, "load() made a second row"


def test_the_instructions_are_paragraphs_not_one_block():
    row = CodebookSettings(instructions="One.\n\nTwo.\n\n\nThree.\n")
    assert row.paragraphs() == ["One.", "Two.", "Three."]


def test_an_admin_can_change_what_coders_are_told(client):
    admin = _user("boss", ADMIN)
    client.force_login(admin)
    response = client.post(
        reverse("review:cin_codebook"), {"instructions": "Read it twice."}
    )
    assert response.status_code == 200
    row = CodebookSettings.load()
    assert row.instructions == "Read it twice."
    # Who changed it, so a later analysis can cut agreement by which
    # wording was live when the decision was made.
    assert row.updated_by == admin


def test_the_classifier_sees_the_edited_instructions(client):
    CodebookSettings.load()
    CodebookSettings.objects.update(instructions="Only this.")
    coder = _user("coder", CLASSIFIER, scope="cohort-1")
    client.force_login(coder)
    body = client.get(reverse("review:classification")).content.decode()
    assert "Only this." in body


def test_markup_in_the_instructions_is_escaped(client):
    """A textarea an admin pastes into from Word is not a place to start
    trusting markup."""
    CodebookSettings.load()
    CodebookSettings.objects.update(instructions="<script>alert(1)</script>")
    coder = _user("coder", CLASSIFIER, scope="cohort-1")
    client.force_login(coder)
    body = client.get(reverse("review:classification")).content.decode()
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


# ------------------------------------------------------------- agreement


def _decide(cohort, article_id, user, primary="", reject=""):
    return ClassificationDecision.objects.create(
        cohort=cohort,
        article_id=article_id,
        decided_by=user,
        primary_label=primary,
        reject_reason=reject,
    )


def test_agreement_is_measured_over_pairs_not_decisions():
    """A rate computed across singly-coded records is not an agreement
    rate, and it reads highest exactly when a cohort has been worked
    least."""
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    alone = _user("a", CLASSIFIER)
    _decide(cohort, "article-1", alone, primary="Sports")
    report = agreement_report()
    assert report["decisions"] == 1
    assert report["pairs"] == 0
    assert report["overall"] is None


def test_two_coders_agreeing_scores_full():
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    one, two = _user("a", CLASSIFIER), _user("b", CLASSIFIER)
    _decide(cohort, "article-1", one, primary="Sports")
    _decide(cohort, "article-1", two, primary="Sports")
    report = agreement_report()
    assert report["pairs"] == 1
    assert report["overall"] == 100.0


def test_a_disagreement_counts_against_both_categories():
    """Charging it only to the category that sorts earlier would
    understate whichever one happens to come second -- and the pair this
    matters for, Civic information against Civic Life, is the one the
    original cohorts disagreed on most."""
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    one, two = _user("a", CLASSIFIER), _user("b", CLASSIFIER)
    _decide(cohort, "article-1", one, primary="Civic information")
    _decide(cohort, "article-1", two, primary="Civic Life")
    named = {row["label"]: row for row in agreement_report()["categories"]}
    assert named["Civic information"]["pairs"] == 1
    assert named["Civic Life"]["pairs"] == 1
    assert named["Civic information"]["rate"] == 0.0
    assert named["Civic Life"]["rate"] == 0.0


def test_two_rejections_for_the_same_reason_agree():
    """They reached the same conclusion about the record. It also stops a
    cohort of unreadable articles scoring as total disagreement."""
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    one, two = _user("a", CLASSIFIER), _user("b", CLASSIFIER)
    _decide(cohort, "article-1", one, reject=ClassificationDecision.NOT_LOCAL)
    _decide(cohort, "article-1", two, reject=ClassificationDecision.NOT_LOCAL)
    assert agreement_report()["overall"] == 100.0


def test_the_worst_category_sorts_first():
    """The page exists to aim a codebook revision, and the revision is
    aimed at whatever coders cannot tell apart."""
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    one, two = _user("a", CLASSIFIER), _user("b", CLASSIFIER)
    _decide(cohort, "article-1", one, primary="Sports")
    _decide(cohort, "article-1", two, primary="Sports")
    _decide(cohort, "article-2", one, primary="Civic information")
    _decide(cohort, "article-2", two, primary="Civic Life")
    labels = [row["label"] for row in agreement_report()["categories"]]
    assert labels[0] in {"Civic information", "Civic Life"}
    assert labels[-1] == "Sports"


# ---------------------------------------------------------------- access


def test_a_classifier_cannot_see_how_they_are_scoring(client):
    """A coder who can watch their own agreement has an incentive to code
    toward the other coders rather than toward the codebook, and the
    measurement stops meaning anything."""
    coder = _user("coder", CLASSIFIER, scope="cohort-1")
    client.force_login(coder)
    for name in ("cin_reporting", "cin_coders", "cin_codebook"):
        assert client.get(reverse(f"review:{name}")).status_code == 403, name


def test_a_classifier_sees_no_cin_section_in_the_nav(client):
    """Absent rather than disabled: a link to a 403 is not access
    control, it is an advertisement for it."""
    coder = _user("coder", CLASSIFIER, scope="cohort-1")
    client.force_login(coder)
    body = client.get(reverse("review:classification")).content.decode()
    assert "cin/codebook" not in body


def test_an_editor_reads_the_reporting_but_does_not_edit_the_codebook(client):
    """Agreement is something an editor should be able to look at. What
    coders are told is a decision about the specification."""
    editor = _user("ed", EDITOR)
    client.force_login(editor)
    assert client.get(reverse("review:cin_reporting")).status_code == 200
    assert client.get(reverse("review:cin_codebook")).status_code == 403


def test_an_admin_reaches_all_three(client):
    admin = _user("boss", ADMIN)
    client.force_login(admin)
    for name in ("cin_reporting", "cin_coders", "cin_codebook"):
        assert client.get(reverse(f"review:{name}")).status_code == 200, name
