"""A round says what a run of coding is for, and who may do it.

Practice answers are never counted. A pilot or training round gives
stories only to coders who have finished a practice and its review. A
paused coder gives back what they had; a removed coder's answers are
withdrawn and their stories coded again.

See docs/CIN_V2_DATADESK.md, WP2 and WP6.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from accounts.privileges import ADMIN, CLASSIFIER
from review.classification import (
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationDecision,
    ClassificationSample,
    CodebookVersion,
    CoderStanding,
    Round,
    agreement_report,
    assign_cohort,
    hold_review,
    open_round,
    practice_review,
    round_report,
    set_standing,
    standings,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

_numbers = iter(range(1, 1000))


def _coder(name):
    return User.objects.create_user(name, email=f"{name}@localnewsimpact.org")


def _batch(round, stories, coders, target=None):
    number = next(_numbers)
    cohort = ClassificationCohort.objects.create(
        number=number,
        slug=f"cohort-{number}",
        round=round,
        codebook_version=round.codebook_version,
        target_coders=target or round.base_coders,
    )
    for story in stories:
        ClassificationSample.objects.create(
            cohort=cohort, article_id=story, stratum="random"
        )
    for user in coders:
        Grant.objects.create(
            user=user, app=DATADESK, scope=cohort.slug, role=CLASSIFIER
        )
    return cohort


def _answer(cohort, user, story, label):
    ClassificationDecision.objects.create(
        cohort=cohort,
        article_id=story,
        decided_by=user,
        primary_label=label,
        codebook_version=cohort.codebook_version,
    )
    ClassificationAssignment.objects.filter(
        cohort=cohort, article_id=story, assigned_to=user
    ).update(completed_at="2026-11-20T00:00:00Z")


def _assigned(cohort, user):
    return set(
        ClassificationAssignment.objects.filter(
            cohort=cohort, assigned_to=user
        ).values_list("article_id", flat=True)
    )


@pytest.fixture
def version(db):
    return CodebookVersion.v1()


@pytest.fixture
def practice(version):
    return open_round("Practice", Round.PRACTICE, codebook_version=version)


@pytest.fixture
def pilot(version):
    return open_round(
        "Pilot", Round.COMPARISON, codebook_version=version, base_coders=3
    )


# --------------------------------------------------------------- practice


def test_in_practice_every_coder_codes_every_story(practice):
    coders = [_coder(n) for n in "abcd"]
    cohort = _batch(practice, ["p1", "p2", "p3"], coders)
    assert assign_cohort(cohort) == 12
    for user in coders:
        assert _assigned(cohort, user) == {"p1", "p2", "p3"}


def test_a_coder_who_joins_later_gets_the_same_practice_stories(practice):
    first = [_coder(n) for n in "abc"]
    cohort = _batch(practice, ["p1", "p2"], first)
    assign_cohort(cohort)
    late = _coder("late")
    Grant.objects.create(user=late, app=DATADESK, scope=cohort.slug, role=CLASSIFIER)
    assert assign_cohort(cohort) == 2
    assert _assigned(cohort, late) == {"p1", "p2"}


def test_practice_answers_are_not_counted_in_agreement(practice, pilot, version):
    a, b = _coder("a"), _coder("b")
    rehearsal = _batch(practice, ["p1"], [a, b])
    assign_cohort(rehearsal)
    _answer(rehearsal, a, "p1", "Sports")
    _answer(rehearsal, b, "p1", "Health")
    assert agreement_report(version)["pairs"] == 0

    for user in (a, b):
        set_standing(user, version, CoderStanding.ADMITTED)
    real = _batch(pilot, ["s1"], [a, b])
    assign_cohort(real)
    _answer(real, a, "s1", "Sports")
    _answer(real, b, "s1", "Sports")
    report = agreement_report(version)
    assert (report["pairs"], report["overall"]) == (1, 100.0)


def test_the_review_is_closed_until_every_coder_has_finished(practice):
    a, b = _coder("a"), _coder("b")
    cohort = _batch(practice, ["p1"], [a, b])
    assign_cohort(cohort)
    _answer(cohort, a, "p1", "Sports")
    review = practice_review(practice)
    assert review["ready"] is False and review["rows"] == []


def test_the_review_puts_the_answers_side_by_side(practice, crawler_schema):
    a, b = _coder("a"), _coder("b")
    cohort = _batch(practice, ["p1", "p2"], [a, b])
    assign_cohort(cohort)
    _answer(cohort, a, "p1", "Sports")
    _answer(cohort, b, "p1", "Health")
    _answer(cohort, a, "p2", "Education")
    _answer(cohort, b, "p2", "Education")
    review = practice_review(practice)
    assert review["ready"] and review["differing"] == 1
    # The stories they answered differently come first.
    first = review["rows"][0]
    assert first["article_id"] == "p1" and first["differ"]
    assert {x["answer"] for x in first["answers"]} == {"Sports", "Health"}
    assert {d["label"] for d in first["definitions"]} == {"Sports", "Health"}


def test_holding_the_review_admits_those_who_finished(practice, version):
    a, b = _coder("a"), _coder("b")
    lead = _coder("lead")
    cohort = _batch(practice, ["p1"], [a, b])
    assign_cohort(cohort)
    _answer(cohort, a, "p1", "Sports")
    assert round_report(practice)["finished"] is False
    _answer(cohort, b, "p1", "Sports")
    admitted = hold_review(practice, by=lead, notes="Narrowed Civic Information.")
    assert set(admitted) == {a, b}
    assert standings(version) == {
        a.id: CoderStanding.ADMITTED,
        b.id: CoderStanding.ADMITTED,
    }
    practice.refresh_from_db()
    assert practice.reviewed_by == lead and "Narrowed" in practice.review_notes
    assert round_report(practice)["finished"] is True


# ------------------------------------------------------ admission to pilot


def test_a_pilot_gives_stories_only_to_admitted_coders(pilot, version):
    ready, new = _coder("ready"), _coder("new")
    set_standing(ready, version, CoderStanding.ADMITTED)
    cohort = _batch(pilot, ["s1", "s2"], [ready, new], target=2)
    assign_cohort(cohort)
    assert _assigned(cohort, ready) == {"s1", "s2"}
    assert _assigned(cohort, new) == set()
    # Admitted later, they are topped up without starting again.
    set_standing(new, version, CoderStanding.ADMITTED)
    assign_cohort(cohort)
    assert _assigned(cohort, new) == {"s1", "s2"}


def test_a_cohort_outside_any_round_is_assigned_as_before(version):
    user = _coder("old")
    cohort = ClassificationCohort.objects.create(number=900, slug="cohort-900")
    ClassificationSample.objects.create(cohort=cohort, article_id="x", stratum="random")
    Grant.objects.create(user=user, app=DATADESK, scope=cohort.slug, role=CLASSIFIER)
    assert assign_cohort(cohort) == 1


# ------------------------------------------------------- pause and remove


def test_pausing_hands_outstanding_stories_to_someone_else(pilot, version):
    a, b, c = (_coder(n) for n in "abc")
    for user in (a, b, c):
        set_standing(user, version, CoderStanding.ADMITTED)
    cohort = _batch(pilot, ["s1", "s2", "s3"], [a, b, c], target=2)
    assign_cohort(cohort)
    done = sorted(_assigned(cohort, a))[0]
    _answer(cohort, a, done, "Sports")

    set_standing(a, version, CoderStanding.PAUSED, note="slow")

    # What they had finished stays theirs; the rest is given back.
    assert _assigned(cohort, a) == {done}
    for story in ("s1", "s2", "s3"):
        holders = ClassificationAssignment.objects.filter(
            cohort=cohort, article_id=story
        ).count()
        assert holders == 2
    assert agreement_report(version)["decisions"] == 1


def test_removing_withdraws_answers_and_the_story_is_coded_again(pilot, version):
    a, b, c = (_coder(n) for n in "abc")
    for user in (a, b, c):
        set_standing(user, version, CoderStanding.ADMITTED)
    cohort = _batch(pilot, ["s1"], [a, b, c], target=2)
    assign_cohort(cohort)
    first, second = (
        ClassificationAssignment.objects.get(pk=pk).assigned_to
        for pk in ClassificationAssignment.objects.filter(cohort=cohort)
        .order_by("id")
        .values_list("pk", flat=True)
    )
    spare = ({a, b, c} - {first, second}).pop()
    _answer(cohort, first, "s1", "Sports")
    _answer(cohort, second, "s1", "Health")
    assert agreement_report(version)["pairs"] == 1

    set_standing(first, version, CoderStanding.REMOVED, note="clicking through")

    # Their answer leaves every figure, and the story has a new coder.
    assert agreement_report(version)["pairs"] == 0
    assert agreement_report(version)["decisions"] == 1
    assert _assigned(cohort, spare) == {"s1"}
    assert round_report(pilot)["decisions"] == 1


# -------------------------------------------------------------- progress


def test_a_pilot_is_finished_when_every_story_has_its_coders(pilot, version):
    coders = [_coder(n) for n in "abc"]
    for user in coders:
        set_standing(user, version, CoderStanding.ADMITTED)
    cohort = _batch(pilot, ["s1", "s2"], coders)
    assign_cohort(cohort)
    for user in coders:
        _answer(cohort, user, "s1", "Sports")
    report = round_report(pilot)
    assert (report["complete_stories"], report["finished"]) == (1, False)
    for user in coders:
        _answer(cohort, user, "s2", "Health")
    assert round_report(pilot)["finished"] is True


def test_a_training_round_is_finished_by_its_rarest_category(version):
    training = open_round(
        "Training",
        Round.COLLECTION,
        codebook_version=version,
        base_coders=2,
        finish_rule={"per_category": 1},
    )
    a, b = _coder("a"), _coder("b")
    for user in (a, b):
        set_standing(user, version, CoderStanding.ADMITTED)
    cohort = _batch(training, ["s1", "s2"], [a, b])
    assign_cohort(cohort)
    _answer(cohort, a, "s1", "Sports")
    _answer(cohort, b, "s1", "Sports")
    # Two coders who differ have not produced a label.
    _answer(cohort, a, "s2", "Health")
    _answer(cohort, b, "s2", "Education")
    report = round_report(training)
    counts = {row["label"]: row["count"] for row in report["categories"]}
    assert counts["Sports"] == 1 and counts["Health"] == 0
    assert report["finished"] is False
    assert report["binding"]["count"] == 0


def test_a_batch_opened_in_a_round_takes_the_rounds_rules(
    pilot, version, crawler_schema
):
    """Its codebook, its coders per story, and the coders already on the
    round: a second batch is not a fresh round of invitations."""
    from review.classification import draw_cohort

    a, b = _coder("a"), _coder("b")
    first = _batch(pilot, ["s1"], [a, b])
    second, _ = draw_cohort(4, for_round=pilot, target_coders=9)
    assert second.round == pilot
    assert second.codebook_version == version
    assert second.target_coders == pilot.base_coders
    granted = set(
        Grant.objects.filter(scope=second.slug, role=CLASSIFIER).values_list(
            "user__username", flat=True
        )
    )
    assert granted == {"a", "b"}
    assert first.round == pilot


# ------------------------------------------------------------- the pages


@pytest.fixture
def admin(client, db):
    user = User.objects.create_user("admin", password="x")
    Grant.objects.create(user=user, app=DATADESK, role=ADMIN, scope="")
    client.force_login(user)
    return user


def _stories(count):
    """Crawler stories a draw can take."""
    from explorer.models import Article

    for n in range(count):
        Article.objects.using("crawler").create(
            id=f"s-{n}",
            url=f"https://example.com/{n}",
            title=f"story {n}",
            text="word " * 150,
            status="labeled",
            wire_check_status="complete",
            primary_label="Sports",
            primary_label_confidence=0.9,
        )


def test_starting_a_round_is_one_step(client, admin, version, crawler_schema):
    """It was six steps on three pages: open a round, open a batch, add
    a coder, invite them, admit them, assign. A name, what the round is
    for and the coders' addresses is the whole of it."""
    from accounts.models import Invitation

    _stories(30)
    known = User.objects.create_user("known", email="known@localnewsimpact.org")
    response = client.post(
        reverse("review:cin_rounds"),
        {
            "name": "V2 practice",
            "kind": Round.PRACTICE,
            "coders": (
                "Known@localnewsimpact.org\nnew.person@example.edu, not-an-address"
            ),
        },
        follow=True,
    )
    body = response.content.decode()
    created = Round.objects.get(name="V2 practice")
    # No codebook chosen: the current one. No numbers: the procedure's.
    assert created.is_practice and created.codebook_version == CodebookVersion.current()
    cohort = created.cohorts.get()
    drawn = cohort.samples.count()
    assert drawn == 20
    # Somebody who has used datadesk has their stories already.
    assert ClassificationAssignment.objects.filter(assigned_to=known).count() == drawn
    # Somebody who has not is waiting for a first sign-in, and the page
    # says that nothing else needs doing.
    assert Invitation.objects.filter(email="new.person@example.edu").exists()
    assert "new.person@example.edu" in body and "Nothing else needs doing" in body
    assert "not-an-address: that is not an address" in body
    assert "20 of 20 stories drawn" in body and "Coders with stories now: 1" in body
    assert "V2 practice" in client.get(reverse("review:cin_rounds")).content.decode()


def test_a_new_coder_gets_their_stories_when_they_first_open_the_queue(
    client, version, crawler_schema
):
    """The grant is made at a first sign-in, and assigning the stories
    used to wait for an admin to notice and press a button."""
    from review.classification import start_round

    _stories(30)
    created, _ = start_round("Practice", Round.PRACTICE, ["late@example.edu"])
    cohort = created.cohorts.get()
    late = User.objects.create_user("late", email="late@example.edu")
    # What the sign-in adapter does with the invitation.
    Grant.objects.create(user=late, app=DATADESK, role=CLASSIFIER, scope=cohort.slug)
    assert not ClassificationAssignment.objects.filter(assigned_to=late).exists()
    client.force_login(late)
    assert client.get(reverse("review:classification")).status_code == 200
    assert ClassificationAssignment.objects.filter(assigned_to=late).count() == 20


def test_a_pilot_started_with_coders_who_have_not_practised_says_so(
    client, admin, version, crawler_schema
):
    from review.classification import ClassificationStory

    for n in range(4):
        ClassificationStory.objects.create(
            id=f"v1:{n}", headline="h", body="b", civic=n < 2, v1_outcome="none"
        )
    ready = User.objects.create_user("ready", email="ready@localnewsimpact.org")
    User.objects.create_user("fresh", email="fresh@localnewsimpact.org")
    set_standing(ready, CodebookVersion.current(), CoderStanding.ADMITTED)
    body = client.post(
        reverse("review:cin_rounds"),
        {
            "name": "Pilot",
            "kind": Round.COMPARISON,
            "coders": "ready@localnewsimpact.org fresh@localnewsimpact.org",
        },
        follow=True,
    ).content.decode()
    created = Round.objects.get(name="Pilot")
    assert created.base_coders == 2
    assert "have not finished a practice round" in body and "fresh" in body
    assert ClassificationAssignment.objects.filter(assigned_to=ready).exists()


def test_a_training_round_starts_with_the_procedures_numbers(
    client, admin, version, crawler_schema
):
    client.post(reverse("review:cin_rounds"), {"name": "T", "kind": Round.COLLECTION})
    created = Round.objects.get(name="T")
    assert created.finish_rule == {"per_category": 250, "holdout": 50}
    assert created.base_coders == 2 and created.third_on_split


def test_the_round_page_records_the_review_and_admits(
    client, admin, practice, version, crawler_schema
):
    a, b = _coder("a"), _coder("b")
    cohort = _batch(practice, ["p1"], [a, b])
    assign_cohort(cohort)
    url = reverse("review:cin_round", args=[practice.pk])
    assert "Opens when every coder has finished" in client.get(url).content.decode()
    _answer(cohort, a, "p1", "Sports")
    _answer(cohort, b, "p1", "Health")
    body = client.post(url, {"action": "review", "notes": "ok"}).content.decode()
    assert "2 coder(s) admitted" in body
    assert standings(version)[a.id] == CoderStanding.ADMITTED


def test_a_coder_cannot_reach_the_rounds_pages(client, practice):
    user = _coder("coder")
    Grant.objects.create(user=user, app=DATADESK, role=CLASSIFIER, scope="cohort-1")
    client.force_login(user)
    assert client.get(reverse("review:cin_rounds")).status_code in (302, 403)
    assert client.get(reverse("review:cin_round", args=[practice.pk])).status_code in (
        302,
        403,
    )


def test_a_coder_can_be_added_before_the_round_has_any_stories(client, db):
    """A coder is granted batches, and a new round has none. Adding
    somebody then used to answer with an error that was easy to miss and
    add nobody. They wait on the round and are given the first batch."""
    from django.contrib.auth import get_user_model
    from django.urls import reverse

    from accounts.models import DATADESK, Grant
    from accounts.privileges import ADMIN
    from review.classification import (
        ClassificationSample,
        _open_batch,
        add_coder_to_round,
        coders_for,
        open_round,
    )

    User = get_user_model()
    admin = User.objects.create_user("admin", email="admin@localnewsimpact.org")
    Grant.objects.create(user=admin, app=DATADESK, role=ADMIN, scope="")
    client.force_login(admin)
    practice = open_round("Practice", Round.PRACTICE)
    url = reverse("review:cin_round", args=[practice.pk])
    body = client.post(
        url, {"action": "invite", "email": "Admin@LocalNewsImpact.org"}
    ).content.decode()
    assert "they are given theirs when a batch is opened" in body
    assert "waiting for a batch to be opened" in body
    practice.refresh_from_db()
    assert practice.waiting_coders == ["admin@localnewsimpact.org"]
    # Twice is once, and something that is not an address is refused.
    assert add_coder_to_round(practice, "admin@localnewsimpact.org") == "waiting"
    practice.refresh_from_db()
    assert len(practice.waiting_coders) == 1
    body = client.post(url, {"action": "invite", "email": "nobody"}).content.decode()
    assert "That is not an address." in body

    cohort = _open_batch(
        practice,
        [ClassificationSample(article_id="a-1", stratum="random")],
        "practice",
    )
    practice.refresh_from_db()
    assert practice.waiting_coders == []
    assert coders_for(cohort) == [admin]
    # With a batch open, adding somebody grants it at once.
    other = User.objects.create_user("b", email="b@localnewsimpact.org")
    assert add_coder_to_round(practice, other.email) == "added"
    assert set(coders_for(cohort)) == {admin, other}


def test_a_practice_round_takes_one_batch(client, db, crawler_schema):
    """Opening a batch is slow, and pressing the button again drew a
    second list of practice stories."""
    from review.classification import ClassificationCohort

    admin = User.objects.create_user("admin", email="admin@localnewsimpact.org")
    Grant.objects.create(user=admin, app=DATADESK, role=ADMIN, scope="")
    client.force_login(admin)
    practice = open_round("Practice", Round.PRACTICE)
    url = reverse("review:cin_round", args=[practice.pk])
    assert client.post(url, {"action": "batch", "size": 20}).status_code == 200
    assert ClassificationCohort.objects.filter(round=practice).count() == 1
    body = client.post(url, {"action": "batch", "size": 20}).content.decode()
    assert "This round already has its stories." in body
    assert ClassificationCohort.objects.filter(round=practice).count() == 1
    # And the button is gone.
    assert "Open a batch</button>" not in client.get(url).content.decode()
