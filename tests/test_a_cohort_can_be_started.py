"""Drawing a cohort and choosing who works it.

The models existed and nothing filled them. A cohort had to be made in a
shell, its articles inserted a statement at a time, and its grants written
straight into the table -- so the queue could be worked and never started.

The sampling design is `docs/CLASSIFICATION_REVIEW_QUEUE.md`: four strata,
confident and uncertain drawn in balance so the comparison between them is
direct, and a random stratum that is not optional because a doubt-ranked
sample finds errors and can never say how many there are.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, WHOLE_APPLICATION, Grant
from accounts.privileges import ADMIN, CLASSIFIER
from explorer.models import Article
from review.classification import (
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationSample,
    assign_cohort,
    coder_report,
    coders_for,
    draw_cohort,
    grant_cohort,
    revoke_cohort,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

CONFIDENT = ClassificationSample.CONFIDENT
UNCERTAIN = ClassificationSample.UNCERTAIN
RANDOM = ClassificationSample.RANDOM
UNLABELLED = ClassificationSample.UNLABELLED


def _article(crawler_schema, n, *, label="Sports", confidence=0.9, status="cleaned"):
    return Article.objects.using("crawler").create(
        id=f"00000000-0000-0000-0000-{n:012d}",
        url=f"https://example.com/{n}",
        title=f"story {n}",
        text="body " * 50,
        status=status,
        wire_check_status="done",
        dataset_id="mizzou",
        primary_label=label,
        primary_label_confidence=confidence,
    )


def _user(username, role=CLASSIFIER, scope="cohort-1"):
    user = User.objects.create_user(username, password="x")
    Grant.objects.create(user=user, app=DATADESK, role=role, scope=scope)
    return user


# ------------------------------------------------------------- the draw


def test_a_cohort_draws_across_the_four_strata(crawler_schema):
    """Each stratum answers a different question, so a draw that filled
    only one would answer only that one."""
    for n in range(8):
        _article(crawler_schema, n, confidence=0.95)  # confident
    for n in range(8, 16):
        _article(crawler_schema, n, confidence=0.2)  # uncertain
    for n in range(16, 24):
        _article(crawler_schema, n, label=None, confidence=None)  # unlabelled

    cohort, drawn = draw_cohort(8)
    assert cohort.number == 1 and cohort.slug == "cohort-1"
    assert drawn[CONFIDENT] == 2
    assert drawn[UNCERTAIN] == 2
    assert drawn[UNLABELLED] == 2
    assert drawn[RANDOM] == 2
    assert ClassificationSample.objects.filter(cohort=cohort).count() == 8


def test_confident_and_uncertain_are_drawn_in_balance(crawler_schema):
    """A model right when sure and wrong when guessing is usable with a
    threshold; one equally wrong in both is a different problem, and an
    unequal draw cannot tell them apart."""
    for n in range(40):
        _article(crawler_schema, n, confidence=0.95 if n % 2 else 0.1)
    _, drawn = draw_cohort(20)
    assert drawn[CONFIDENT] == drawn[UNCERTAIN]


def test_the_middle_band_is_in_neither_doubt_stratum(crawler_schema):
    """0.5-0.7 is neither claim. Including it blurs the one thing those
    two strata exist to separate."""
    for n in range(12):
        _article(crawler_schema, n, confidence=0.6)
    _, drawn = draw_cohort(8)
    assert drawn[CONFIDENT] == 0
    assert drawn[UNCERTAIN] == 0
    # Still reachable, which is the other half of the rule.
    assert drawn[RANDOM] == 2


def test_a_short_stratum_is_reported_not_topped_up(crawler_schema):
    """A cohort whose unlabelled quarter came out of the random pool is
    not the cohort the design describes, and a caller that cannot see the
    difference cannot correct for it."""
    for n in range(20):
        _article(crawler_schema, n, confidence=0.95)
    _, drawn = draw_cohort(20)
    assert drawn[UNLABELLED] == 0
    assert drawn[CONFIDENT] == 5
    assert sum(drawn.values()) < 20


def test_the_inclusion_probability_is_recorded(crawler_schema):
    """A rate measured over rows that were not equally likely to be drawn
    is not a rate, and the chance cannot be recovered afterwards."""
    for n in range(40):
        _article(crawler_schema, n, confidence=0.95)
    cohort, _ = draw_cohort(20)
    rows = ClassificationSample.objects.filter(cohort=cohort, stratum=CONFIDENT)
    assert rows.exists()
    for row in rows:
        assert 0 < row.inclusion_probability <= 1
    # 5 confident drawn from 40 eligible.
    assert rows.first().inclusion_probability == pytest.approx(5 / 40)


def test_an_article_is_never_in_two_cohorts(crawler_schema):
    """Membership is permanent. One article in two cohorts makes each of
    their rates depend on the other."""
    for n in range(12):
        _article(crawler_schema, n, confidence=0.95)
    first, _ = draw_cohort(4)
    second, _ = draw_cohort(4)
    a = set(
        ClassificationSample.objects.filter(cohort=first).values_list(
            "article_id", flat=True
        )
    )
    b = set(
        ClassificationSample.objects.filter(cohort=second).values_list(
            "article_id", flat=True
        )
    )
    assert not (a & b)


def test_only_classifiable_statuses_are_drawn(crawler_schema):
    """The queue reviews the population the model is actually fed, not a
    wider one: `analysis.py` classifies `cleaned` and `local` only."""
    for n in range(8):
        _article(crawler_schema, n, confidence=0.95, status="wire")
    _, drawn = draw_cohort(8)
    assert sum(drawn.values()) == 0


def test_the_cohort_number_increments(crawler_schema):
    _article(crawler_schema, 1, confidence=0.95)
    first, _ = draw_cohort(1)
    second, _ = draw_cohort(1)
    assert (first.number, second.number) == (1, 2)
    assert second.slug == "cohort-2"


# -------------------------------------------------------- who works it


def test_a_classifier_grant_can_never_be_application_wide(crawler_schema):
    """`WHOLE_APPLICATION` is the empty string and `permitted_scopes`
    returns ALL_SCOPES for it, so an unscoped classifier grant would let
    that person work every cohort there is -- the opposite of the point
    of scoping the role."""
    user = User.objects.create_user("nobody", password="x")
    cohort = ClassificationCohort.objects.create(number=1, slug=WHOLE_APPLICATION)
    with pytest.raises(ValueError):
        grant_cohort(user, cohort)
    assert not Grant.objects.filter(user=user, role=CLASSIFIER).exists()


def test_granting_is_idempotent(crawler_schema):
    user = User.objects.create_user("coder", password="x")
    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    grant_cohort(user, cohort)
    grant_cohort(user, cohort)
    assert (
        Grant.objects.filter(user=user, role=CLASSIFIER, scope="cohort-1").count() == 1
    )
    assert coders_for(cohort) == [user]


def test_revoking_withdraws_outstanding_work_but_keeps_decisions(crawler_schema):
    """A cohort that waits forever on somebody who can no longer open it
    is the quiet way this design fails."""
    _article(crawler_schema, 1, confidence=0.95)
    cohort, _ = draw_cohort(4)
    user = User.objects.create_user("coder", password="x")
    grant_cohort(user, cohort)
    assign_cohort(cohort)
    assert ClassificationAssignment.objects.filter(assigned_to=user).exists()
    withdrawn = revoke_cohort(user, cohort)
    assert withdrawn >= 1
    assert not Grant.objects.filter(user=user, scope="cohort-1").exists()
    assert not ClassificationAssignment.objects.filter(
        assigned_to=user, completed_at__isnull=True
    ).exists()


# ------------------------------------------------------- the assignment


def _cohort_with(crawler_schema, articles, coders, target=3):
    for n in range(articles):
        _article(crawler_schema, n, confidence=0.95)
    cohort, _ = draw_cohort(articles * 4, target_coders=target)
    people = []
    for i in range(coders):
        user = User.objects.create_user(f"coder{i}", password="x")
        grant_cohort(user, cohort)
        people.append(user)
    return cohort, people


def test_every_record_reaches_the_target_number_of_coders(crawler_schema):
    cohort, people = _cohort_with(crawler_schema, 6, 3, target=3)
    assign_cohort(cohort)
    drawn = ClassificationSample.objects.filter(cohort=cohort).count()
    for article_id in ClassificationSample.objects.filter(cohort=cohort).values_list(
        "article_id", flat=True
    ):
        assert (
            ClassificationAssignment.objects.filter(
                cohort=cohort, article_id=article_id
            ).count()
            == 3
        )
    assert ClassificationAssignment.objects.filter(cohort=cohort).count() == drawn * 3


def test_the_load_is_even_across_coders(crawler_schema):
    """An uneven split makes a cohort finish at the pace of its slowest
    member, and "assign coders to assure each gets the same number" is
    the requirement."""
    cohort, people = _cohort_with(crawler_schema, 8, 4, target=2)
    assign_cohort(cohort)
    counts = [
        ClassificationAssignment.objects.filter(cohort=cohort, assigned_to=p).count()
        for p in people
    ]
    assert max(counts) - min(counts) <= 1, counts


def test_nobody_gets_the_same_record_twice(crawler_schema):
    """Two decisions from one person on one article is not the overlap
    agreement is measured over."""
    cohort, people = _cohort_with(crawler_schema, 4, 2, target=3)
    assign_cohort(cohort)
    for article_id in ClassificationSample.objects.filter(cohort=cohort).values_list(
        "article_id", flat=True
    ):
        holders = list(
            ClassificationAssignment.objects.filter(
                cohort=cohort, article_id=article_id
            ).values_list("assigned_to_id", flat=True)
        )
        assert len(holders) == len(set(holders))
        # Two coders, target three: as many as exist, not a duplicate.
        assert len(holders) == 2


def test_assigning_again_tops_up_rather_than_starting_over(crawler_schema):
    cohort, people = _cohort_with(crawler_schema, 4, 2, target=3)
    first = assign_cohort(cohort)
    assert assign_cohort(cohort) == 0, "a second run duplicated work"
    third = User.objects.create_user("late", password="x")
    grant_cohort(third, cohort)
    added = assign_cohort(cohort)
    drawn = ClassificationSample.objects.filter(cohort=cohort).count()
    assert added == drawn, "the new coder did not pick up every record"
    assert (
        ClassificationAssignment.objects.filter(cohort=cohort).count() == first + added
    )


def test_a_cohort_with_no_coders_assigns_nothing(crawler_schema):
    _article(crawler_schema, 1, confidence=0.95)
    cohort, _ = draw_cohort(4)
    assert assign_cohort(cohort) == 0


# ---------------------------------------------------------- through the UI


def test_an_admin_draws_a_cohort_from_the_page(client, crawler_schema):
    for n in range(20):
        _article(crawler_schema, n, confidence=0.95)
    admin = _user("boss", role=ADMIN, scope="")
    client.force_login(admin)
    response = client.post(
        reverse("review:cin_coders"),
        {"action": "draw", "size": "8", "target_coders": "3", "note": "first pass"},
    )
    assert response.status_code == 200
    cohort = ClassificationCohort.objects.get()
    assert cohort.target_coders == 3
    assert cohort.note == "first pass"
    assert ClassificationSample.objects.filter(cohort=cohort).exists()


def test_the_page_says_when_a_stratum_ran_short(client, crawler_schema):
    """Silence here would read as a full cohort."""
    for n in range(20):
        _article(crawler_schema, n, confidence=0.95)
    admin = _user("boss", role=ADMIN, scope="")
    client.force_login(admin)
    body = client.post(
        reverse("review:cin_coders"),
        {"action": "draw", "size": "20", "target_coders": "3"},
    ).content.decode()
    assert "ran short" in body


def test_an_admin_grants_and_assigns_from_the_page(client, crawler_schema):
    for n in range(8):
        _article(crawler_schema, n, confidence=0.95)
    cohort, _ = draw_cohort(4)
    coder = User.objects.create_user("coder", password="x")
    admin = _user("boss", role=ADMIN, scope="")
    client.force_login(admin)

    client.post(
        reverse("review:cin_coders"),
        {"action": "grant", "cohort": cohort.slug, "user": str(coder.pk)},
    )
    assert coders_for(cohort) == [coder]

    client.post(
        reverse("review:cin_coders"), {"action": "assign", "cohort": cohort.slug}
    )
    assert ClassificationAssignment.objects.filter(assigned_to=coder).exists()


def test_a_zero_sized_cohort_is_refused(client, crawler_schema):
    admin = _user("boss", role=ADMIN, scope="")
    client.force_login(admin)
    body = client.post(
        reverse("review:cin_coders"), {"action": "draw", "size": "0"}
    ).content.decode()
    assert "at least one article" in body
    assert not ClassificationCohort.objects.exists()


def test_the_report_shows_how_far_short_of_target_a_cohort_is(crawler_schema):
    """A cohort cannot be used for training until every record has been
    disposed of by the target number of people, and one absent coder
    leaving records short is the predictable failure."""
    cohort, people = _cohort_with(crawler_schema, 4, 2, target=2)
    assign_cohort(cohort)
    row = next(r for r in coder_report()["cohorts"] if r["cohort"] == cohort)
    assert row["granted"] == 2
    assert row["assignments"] == row["needed"]
    assert row["short"] == 0
    assert row["done"] == 0


# ------------------------------------------------ inviting straight in


def test_a_coder_is_invited_onto_a_cohort_not_to_a_dataset(crawler_schema):
    """The flow was backwards: invited as a viewer, sign in so a user
    row exists, then given a cohort. Three steps to grant one role, two
    of which handed them access nobody wanted them to have."""
    from accounts.models import Invitation
    from review.classification import invite_coder

    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    kind, invitation = invite_coder("newcoder@example.org", cohort)
    assert kind == "invited"
    assert invitation.role == CLASSIFIER
    assert invitation.scope == "cohort-1"
    assert Invitation.objects.count() == 1


def test_an_in_domain_address_can_be_invited_too(crawler_schema):
    """`accounts.views.invite` refuses these, correctly, for admission --
    the domain already admits them. But admission and role are different
    questions, and refusing both blocks pre-assigning a cohort to
    somebody who has not signed in yet, which is the whole complaint."""
    from accounts.models import Invitation
    from review.classification import invite_coder

    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    kind, _ = invite_coder("staffer@missouri.edu", cohort)
    assert kind == "invited"
    assert Invitation.objects.get().scope == "cohort-1"


def test_an_existing_user_is_granted_immediately(crawler_schema):
    """No point waiting for a sign-in that already happened."""
    from accounts.models import Invitation
    from review.classification import invite_coder

    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    user = User.objects.create_user("known", password="x", email="known@example.org")
    kind, _ = invite_coder("KNOWN@example.org", cohort)
    assert kind == "granted"
    assert coders_for(cohort) == [user]
    assert not Invitation.objects.exists()


def test_inviting_the_same_address_twice_updates_the_row(crawler_schema):
    """`Invitation.email` is unique, so a second cohort for the same
    person must not fail on the constraint."""
    from accounts.models import Invitation
    from review.classification import invite_coder

    first = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    second = ClassificationCohort.objects.create(number=2, slug="cohort-2")
    invite_coder("coder@example.org", first)
    invite_coder("coder@example.org", second)
    assert Invitation.objects.count() == 1
    assert Invitation.objects.get().scope == "cohort-2"


def test_the_invitation_grants_only_the_cohort_on_first_sign_in(crawler_schema):
    """A classifier holds `classify` on one cohort and nothing else. An
    invitation that also made them a viewer would reintroduce exactly the
    access this removes."""
    from review.classification import invite_coder

    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    invite_coder("arriving@example.org", cohort)
    user = User.objects.create_user(
        "arriving", password="x", email="arriving@example.org"
    )
    # What the adapter does on first sign-in, without a Google round trip.
    from accounts.models import Invitation

    invitation = Invitation.for_email(user.email)
    Grant.objects.get_or_create(
        user=user,
        app=DATADESK,
        scope=invitation.scope,
        defaults={"role": invitation.role},
    )
    grants = list(Grant.objects.filter(user=user).values_list("role", "scope"))
    assert grants == [(CLASSIFIER, "cohort-1")]


def test_a_bad_address_is_refused(crawler_schema):
    from review.classification import invite_coder

    cohort = ClassificationCohort.objects.create(number=1, slug="cohort-1")
    with pytest.raises(ValueError):
        invite_coder("not-an-address", cohort)


def test_an_admin_invites_from_the_page(client, crawler_schema):
    from accounts.models import Invitation

    _article(crawler_schema, 1, confidence=0.95)
    cohort, _ = draw_cohort(4)
    admin = _user("boss", role=ADMIN, scope="")
    client.force_login(admin)
    body = client.post(
        reverse("review:cin_coders"),
        {"action": "invite", "cohort": cohort.slug, "email": "fresh@example.org"},
    ).content.decode()
    assert "first sign in" in body
    assert Invitation.objects.filter(email="fresh@example.org").exists()


def test_the_page_lists_who_has_not_turned_up(client, crawler_schema):
    """Without this they are invisible: no user row exists, so an admin
    who invited somebody last week cannot see that they never came."""
    from review.classification import invite_coder

    _article(crawler_schema, 1, confidence=0.95)
    cohort, _ = draw_cohort(4)
    invite_coder("waiting@example.org", cohort)
    admin = _user("boss", role=ADMIN, scope="")
    client.force_login(admin)
    body = client.get(reverse("review:cin_coders")).content.decode()
    assert "waiting@example.org" in body
    assert "not yet signed in" in body
