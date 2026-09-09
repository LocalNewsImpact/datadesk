"""A classifier labels the stories it is assigned, and nothing else.

The classification queue collects human CIN labels to measure the
production model against and to retrain it. Two things have to be
controlled for those labels to be worth anything: **who** does the
classifying, and **which records** they are given. Neither survives
being expressed as a rung on the privilege ladder.

A rung carries everything beneath it. So a classifier placed on the
ladder would either hold `read` over the whole corpus — and then a coder
can choose what to label, and a sample somebody chose is not a sample —
or hand `classify` to every viewer and designer above it, and then there
is no saying who is doing the classification.

So `classifier` sits outside the ladder with `classify` and nothing
else, and `classify` is granted on the ladder only at editor, who runs
the programme.

See docs/CLASSIFICATION_REVIEW_QUEUE.md section 4.
"""

import pytest

from accounts.privileges import (
    ADMIN,
    CLASSIFIER,
    CLASSIFY,
    CREATE,
    DESIGN,
    DESIGNER,
    EDITOR,
    READ,
    REVIEWER,
    ROLE_ADDS,
    ROLE_CHOICES,
    ROLE_PRIVILEGES,
    ROLES,
    VIEWER,
    WRITE,
)


def test_a_classifier_holds_classify_and_nothing_else():
    assert ROLE_PRIVILEGES[CLASSIFIER] == frozenset({CLASSIFY})


def test_a_classifier_cannot_read_the_corpus():
    """The one that matters most. A coder who can read the corpus can
    choose what to label, and a sample somebody chose is not a sample.
    The queue serves them their assignments; that is the whole of their
    access."""
    assert READ not in ROLE_PRIVILEGES[CLASSIFIER]


def test_a_classifier_cannot_correct_the_records_it_labels():
    """A labeller who can edit the article is a labeller whose labels
    describe an article they may have changed."""
    for forbidden in (WRITE, CREATE, DESIGN):
        assert forbidden not in ROLE_PRIVILEGES[CLASSIFIER], forbidden


def test_it_is_not_a_rung():
    """It cannot be. Inheritance is what breaks both requirements."""
    assert CLASSIFIER not in ROLES


@pytest.mark.parametrize("role", [VIEWER, DESIGNER, REVIEWER])
def test_the_ladder_below_editor_cannot_classify(role):
    """Otherwise there is no saying who is doing the classification: a
    designer granted an account to draw a chart would be able to label
    the training set."""
    assert CLASSIFY not in ROLE_PRIVILEGES[role], role


@pytest.mark.parametrize("role", [EDITOR, ADMIN])
def test_an_editor_runs_the_programme(role):
    """Editors draw the cohorts, read the agreement and decide what is
    settled, so `classify` arrives at that rung rather than lower."""
    assert CLASSIFY in ROLE_PRIVILEGES[role]


def test_the_ladder_still_holds_for_the_rungs():
    """Adding a privilege at editor must not break the property the
    ladder is written to keep."""
    for lower, higher in zip(ROLES, ROLES[1:], strict=False):
        assert ROLE_PRIVILEGES[lower] <= ROLE_PRIVILEGES[higher], higher
        gained = ROLE_PRIVILEGES[higher] - ROLE_PRIVILEGES[lower]
        assert gained == ROLE_ADDS[higher], higher


def test_the_role_can_be_granted():
    """A role absent from ROLE_CHOICES is right in the privilege table
    and unusable in the admin."""
    assert CLASSIFIER in dict(ROLE_CHOICES)


# --------------------------------------------- what a classifier can see


@pytest.fixture
def classifier(db):
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant

    user = User.objects.create_user("cl", email="cl@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role=CLASSIFIER)
    return user


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_classifier_is_offered_nothing_it_cannot_reach(classifier):
    """Every existing section requires `read` or more, and a classifier
    holds neither. Until the classification queue exists there is
    genuinely nothing for them to open, and offering a link that refuses
    them at the door would be worse than an empty console."""
    from accounts.sections import groups_for

    labels = {
        section["label"]
        for group in groups_for(classifier)
        for section in group["sections"]
    }
    for other in ("Extraction", "Discovery", "Sources", "Articles", "Visuals"):
        assert other not in labels, labels


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_reviewer_still_sees_the_correction_queues(db):
    """The narrowing is the classifier's, not everyone's."""
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant
    from accounts.sections import groups_for

    user = User.objects.create_user("rv", email="rv@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role=REVIEWER)
    labels = {
        section["label"] for group in groups_for(user) for section in group["sections"]
    }
    assert "Extraction" in labels and "Discovery" in labels


def test_a_classification_section_is_flagged_by_the_privilege_it_needs():
    """ "Flagged as classification task support" is not a second field to
    keep in step with the access check — it IS the access check."""
    from accounts.sections import CLASSIFIER as CLASSIFIER_SECTION

    assert CLASSIFIER_SECTION == CLASSIFY
