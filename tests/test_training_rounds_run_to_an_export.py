"""A training round: a third coder on a disagreement, check stories
hidden in the queue, and the labels exported for the retrain.

See docs/CIN_V2_DATADESK.md, WP5, WP6 and WP9.
"""

import csv
import io

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from accounts.privileges import ADMIN, CLASSIFIER
from review.classification import (
    CHECK_STORIES,
    CheckResult,
    CheckStory,
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationDecision,
    ClassificationSample,
    ClassificationStory,
    CodebookVersion,
    CoderStanding,
    Round,
    V1Decision,
    add_third_coder,
    agreement_report,
    assign_cohort,
    check_figures,
    find_check_stories,
    hide_check_stories,
    judge_check_stories,
    open_round,
    set_standing,
    standings,
    training_rows,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/review/classification/"
_numbers = iter(range(1, 1000))


def _coders(names, version):
    out = []
    for name in names:
        user = User.objects.create_user(name, email=f"{name}@localnewsimpact.org")
        set_standing(user, version, CoderStanding.ADMITTED)
        out.append(user)
    return out


def _batch(for_round, stories, coders, holdout=()):
    number = next(_numbers)
    cohort = ClassificationCohort.objects.create(
        number=number,
        slug=f"cohort-{number}",
        round=for_round,
        codebook_version=for_round.codebook_version,
        target_coders=for_round.base_coders,
    )
    for story in stories:
        ClassificationSample.objects.create(
            cohort=cohort,
            article_id=story,
            stratum="category",
            inclusion_probability=0.5,
            holdout=story in holdout,
        )
    for user in coders:
        Grant.objects.create(
            user=user, app=DATADESK, scope=cohort.slug, role=CLASSIFIER
        )
    return cohort


def _answer(cohort, user, story, label="", secondary="", check=False):
    ClassificationDecision.objects.create(
        cohort=cohort,
        article_id=story,
        decided_by=user,
        primary_label=label,
        secondary_label=secondary,
        reject_reason="" if label else "not_local",
        codebook_version=cohort.codebook_version,
        is_check=check,
    )
    ClassificationAssignment.objects.filter(
        cohort=cohort, article_id=story, assigned_to=user
    ).update(completed_at="2026-11-20T00:00:00Z")


def _on(cohort, story):
    return set(
        ClassificationAssignment.objects.filter(
            cohort=cohort, article_id=story
        ).values_list("assigned_to__username", flat=True)
    )


@pytest.fixture
def version(db):
    return CodebookVersion.v1()


@pytest.fixture
def training(version):
    return open_round(
        "Training",
        Round.COLLECTION,
        codebook_version=version,
        base_coders=2,
        third_on_split=True,
        finish_rule={"per_category": 5, "holdout": 1},
    )


# ---------------------------------------------------- third on a split


def test_two_coders_who_match_need_no_third(training, version):
    a, b, c = _coders("abc", version)
    cohort = _batch(training, ["s1"], [a, b, c])
    assign_cohort(cohort)
    first, second = (
        User.objects.get(username=name) for name in sorted(_on(cohort, "s1"))
    )
    _answer(cohort, first, "s1", "Sports")
    _answer(cohort, second, "s1", "Sports")
    assert add_third_coder(cohort, "s1") is None
    assert len(_on(cohort, "s1")) == 2


def test_two_coders_who_differ_get_a_third_who_has_not_seen_it(training, version):
    a, b, c = _coders("abc", version)
    cohort = _batch(training, ["s1"], [a, b, c])
    assign_cohort(cohort)
    pair = sorted(_on(cohort, "s1"))
    first, second = (User.objects.get(username=name) for name in pair)
    _answer(cohort, first, "s1", "Sports")
    assert add_third_coder(cohort, "s1") is None  # only one answer so far
    _answer(cohort, second, "s1", "Health")
    third = add_third_coder(cohort, "s1")
    assert third.assigned_to.username == ({"a", "b", "c"} - set(pair)).pop()
    # Asked again, it does nothing more.
    assert add_third_coder(cohort, "s1") is None
    assert len(_on(cohort, "s1")) == 3


def test_a_category_against_a_rejection_is_a_split(training, version):
    a, b, c = _coders("abc", version)
    cohort = _batch(training, ["s1"], [a, b, c])
    assign_cohort(cohort)
    first, second = (
        User.objects.get(username=name) for name in sorted(_on(cohort, "s1"))
    )
    _answer(cohort, first, "s1", "Sports")
    _answer(cohort, second, "s1", "")
    assert add_third_coder(cohort, "s1") is not None


def test_two_rejections_get_nobody_else(training, version):
    """They agreed it is not a story to label."""
    a, b, c = _coders("abc", version)
    cohort = _batch(training, ["s1"], [a, b, c])
    assign_cohort(cohort)
    first, second = (
        User.objects.get(username=name) for name in sorted(_on(cohort, "s1"))
    )
    _answer(cohort, first, "s1", "")
    _answer(cohort, second, "s1", "")
    assert add_third_coder(cohort, "s1") is None


def test_a_round_that_does_not_ask_for_a_third_gets_none(version):
    plain = open_round("Plain", Round.COLLECTION, base_coders=2)
    a, b, c = _coders("abc", version)
    cohort = _batch(plain, ["s1"], [a, b, c])
    assign_cohort(cohort)
    first, second = (
        User.objects.get(username=name) for name in sorted(_on(cohort, "s1"))
    )
    _answer(cohort, first, "s1", "Sports")
    _answer(cohort, second, "s1", "Health")
    assert add_third_coder(cohort, "s1") is None


def test_the_third_is_added_when_the_second_answer_is_saved(
    client, training, version, crawler_schema
):
    from explorer.models import Article, CandidateLink, Source

    source = Source.objects.using("crawler").create(
        id="s1", host="a.example", canonical_name="A"
    )
    link = CandidateLink.objects.using("crawler").create(
        id="cl-1", url="https://a.example/1", source=source, status="extracted"
    )
    Article.objects.using("crawler").create(
        id="a-1",
        candidate_link=link,
        url=link.url,
        title="T",
        status="labeled",
        text="word " * 20,
    )
    a, b, c = _coders("abc", version)
    cohort = _batch(training, ["a-1"], [a, b, c])
    assign_cohort(cohort)
    pair = sorted(_on(cohort, "a-1"))
    for name, label in zip(pair, ("Sports", "Health"), strict=True):
        user = User.objects.get(username=name)
        client.force_login(user)
        mine = ClassificationAssignment.objects.get(cohort=cohort, assigned_to=user)
        client.post(URL, {"assignment": mine.pk, "primary": label})
    assert len(_on(cohort, "a-1")) == 3


# -------------------------------------------------------- check stories


def _pilot_with_unanimous(version, coders, count):
    pilot = open_round("Pilot", Round.COMPARISON, codebook_version=version)
    cohort = _batch(pilot, [f"p{i}" for i in range(count)] + ["split"], coders)
    for i in range(count):
        for user in coders:
            _answer(cohort, user, f"p{i}", "Sports")
    for user, label in zip(coders, ("Sports", "Health", "Sports"), strict=True):
        _answer(cohort, user, "split", label)
    return pilot


def test_check_stories_are_the_ones_every_pilot_coder_agreed_on(version):
    pilots = _coders(["p1", "p2", "p3"], version)
    _pilot_with_unanimous(version, pilots, 4)
    assert find_check_stories(version) == 4
    assert find_check_stories(version) == 4  # asked twice, the same four
    assert not CheckStory.objects.filter(article_id="split").exists()
    assert set(CheckStory.objects.values_list("answer", flat=True)) == {"Sports"}


def test_a_coder_is_only_given_check_stories_they_have_not_coded(training, version):
    pilots = _coders(["p1", "p2", "p3"], version)
    _pilot_with_unanimous(version, pilots, 15)
    find_check_stories(version)
    (new,) = _coders(["new"], version)
    cohort = _batch(training, ["s1"], [new, pilots[0]])
    assign_cohort(cohort)
    hidden = hide_check_stories(cohort)
    checks = ClassificationAssignment.objects.filter(is_check=True)
    # Ten for the new coder. The pilot coder has coded every one already.
    assert hidden == 10
    assert set(checks.values_list("assigned_to__username", flat=True)) == {"new"}
    # A second batch tops up, never past the number a coder is judged on.
    for _ in range(5):
        hide_check_stories(_batch(training, [], [new]))
    assert checks.filter(assigned_to=new).count() == 15 <= CHECK_STORIES


def test_a_check_answer_is_counted_nowhere_else(training, version):
    pilots = _coders(["p1", "p2", "p3"], version)
    _pilot_with_unanimous(version, pilots, 3)
    find_check_stories(version)
    before = agreement_report(version)["decisions"]
    (new,) = _coders(["new"], version)
    cohort = _batch(training, [], [new])
    hide_check_stories(cohort)
    _answer(cohort, new, "p0", "Health", check=True)
    assert agreement_report(version)["decisions"] == before
    assert check_figures(training) == {new.id: (1, 1)}
    assert all(
        row["story_id"] != "p0" or row["round"] == "Pilot"
        for row in training_rows(version)
    )


def _check_answers(cohort, user, total, misses):
    for i in range(total):
        CheckStory.objects.get_or_create(
            codebook_version=cohort.codebook_version,
            article_id=f"chk-{user.username}-{i}",
            defaults={"answer": "Sports"},
        )
        _answer(
            cohort,
            user,
            f"chk-{user.username}-{i}",
            "Health" if i < misses else "Sports",
            check=True,
        )


def test_a_coder_far_over_the_normal_miss_rate_is_paused(training, version):
    good, careless = _coders(["good", "careless"], version)
    cohort = _batch(training, ["s1", "s2"], [good, careless])
    assign_cohort(cohort)
    _check_answers(cohort, good, 40, 4)  # the normal rate: 1 in 10
    _check_answers(cohort, careless, 40, 16)
    results = {r.user.username: r for r in judge_check_stories(training)}
    # At 1 in 10 over 40 stories, a coder is paused at 10 misses.
    assert results["careless"].threshold == 10 and results["careless"].paused
    assert results["careless"].normal_miss_rate == pytest.approx(0.1)
    assert standings(version)[careless.id] == CoderStanding.PAUSED
    # Their outstanding stories went back; the careful coder is untouched.
    assert not ClassificationAssignment.objects.filter(
        assigned_to=careless, completed_at__isnull=True, is_check=False
    ).exists()
    assert results["good"].paused is False
    assert standings(version)[good.id] == CoderStanding.ADMITTED


def test_a_coder_is_judged_once_and_not_before_forty(training, version):
    a, b = _coders("ab", version)
    cohort = _batch(training, [], [a, b])
    _check_answers(cohort, a, 39, 30)
    _check_answers(cohort, b, 40, 4)
    # b has forty but a, the only other coder, has not: no rate to judge by.
    assert judge_check_stories(training) == []
    _check_answers(_batch(training, [], [a]), a, 0, 0)
    CheckStory.objects.create(
        codebook_version=version, article_id="one-more", answer="Sports"
    )
    _answer(cohort, a, "one-more", "Sports", check=True)
    assert len(judge_check_stories(training)) == 2
    assert judge_check_stories(training) == []
    assert CheckResult.objects.count() == 2


# ---------------------------------------------------------------- export


def test_the_export_holds_one_row_per_labelled_story(training, version):
    a, b, c = _coders("abc", version)
    cohort = _batch(
        training,
        ["agree", "weak", "none", "rejected", "held"],
        [a, b, c],
        holdout={"held"},
    )
    _answer(cohort, a, "agree", "Sports", secondary="Health")
    _answer(cohort, b, "agree", "Sports")
    _answer(cohort, a, "weak", "Sports")
    _answer(cohort, b, "weak", "Health", secondary="Sports")
    _answer(cohort, a, "none", "Sports")
    _answer(cohort, b, "none", "Health")
    _answer(cohort, a, "rejected", "")
    _answer(cohort, b, "rejected", "")
    _answer(cohort, a, "held", "Education")
    _answer(cohort, b, "held", "Education")
    rows = {row["story_id"]: row for row in training_rows(version)}
    assert set(rows) == {"agree", "weak", "held"}
    assert rows["agree"]["confidence"] == 0.9 and rows["weak"]["confidence"] == 0.7
    assert (rows["agree"]["secondary"], rows["agree"]["secondary_weight"]) == (
        "Health",
        0.5,
    )
    assert rows["agree"]["split"] == "train" and rows["held"]["split"] == "holdout"
    assert rows["agree"]["inclusion_probability"] == 0.5
    assert rows["agree"]["round"] == "Training" and rows["agree"]["n_coders"] == 2


def test_practice_and_removed_coders_are_not_exported(training, version):
    a, b, c = _coders("abc", version)
    practice = open_round("Practice", Round.PRACTICE, codebook_version=version)
    rehearsal = _batch(practice, ["p"], [a, b])
    _answer(rehearsal, a, "p", "Sports")
    _answer(rehearsal, b, "p", "Sports")
    cohort = _batch(training, ["s"], [a, b, c])
    _answer(cohort, a, "s", "Sports")
    _answer(cohort, b, "s", "Sports")
    assert [row["story_id"] for row in training_rows(version)] == ["s"]
    set_standing(a, version, CoderStanding.REMOVED)
    assert training_rows(version) == []


def test_v1_stories_keep_their_v1_label_unless_they_were_piloted(training, version):
    for key, outcome, label in (
        ("v1:1", ClassificationStory.LABEL_09, "Sports"),
        ("v1:2", ClassificationStory.LABEL_07, "Health"),
        ("v1:3", ClassificationStory.REJECTED, ""),
        ("v1:4", ClassificationStory.NO_LABEL, ""),
        ("v1:5", ClassificationStory.LABEL_09, "Civic Life"),
    ):
        story = ClassificationStory.objects.create(
            id=key, v1_outcome=outcome, v1_label=label
        )
        V1Decision.objects.create(story=story, group="A", primary_label=label)
        V1Decision.objects.create(story=story, group="C", primary_label=label)
    a, b, c = _coders("abc", version)
    pilot = open_round("Pilot", Round.COMPARISON, codebook_version=version)
    cohort = _batch(pilot, ["v1:5"], [a, b, c])
    for user in (a, b, c):
        _answer(cohort, user, "v1:5", "Economic Development")
    rows = {row["story_id"]: row for row in training_rows(version)}
    assert set(rows) == {"v1:1", "v1:2", "v1:5"}
    assert (rows["v1:1"]["label"], rows["v1:1"]["codebook_version"]) == ("Sports", "V1")
    assert rows["v1:2"]["confidence"] == 0.7 and rows["v1:1"]["n_coders"] == 2
    # Piloted: the label V2 coders agreed on, not the one V1 gave it.
    assert rows["v1:5"]["label"] == "Economic Development"
    assert rows["v1:5"]["round"] == "Pilot"


def test_the_export_can_use_the_names_coders_saw(db):
    from review.classification import CodebookSettings

    draft = CodebookSettings.load()
    draft.display_names = {"Civic Life": "Community Life"}
    draft.save()
    v2 = draft.freeze("V2.0")
    training = open_round(
        "Training", Round.COLLECTION, codebook_version=v2, base_coders=2
    )
    a, b = _coders("ab", v2)
    cohort = _batch(training, ["s"], [a, b])
    _answer(cohort, a, "s", "Civic Life")
    _answer(cohort, b, "s", "Civic Life")
    assert training_rows(v2)[0]["label"] == "Civic Life"
    assert training_rows(v2, shown_names=True)[0]["label"] == "Community Life"


def test_an_admin_downloads_the_export(client, training, version):
    admin = User.objects.create_user("admin", password="x")
    Grant.objects.create(user=admin, app=DATADESK, role=ADMIN, scope="")
    a, b = _coders("ab", version)
    cohort = _batch(training, ["s"], [a, b])
    _answer(cohort, a, "s", "Sports")
    _answer(cohort, b, "s", "Sports")
    client.force_login(admin)
    response = client.get(reverse("review:cin_export"), {"codebook": "V1"})
    assert response["Content-Type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.content.decode())))
    assert [(r["story_id"], r["label"], r["split"]) for r in rows] == [
        ("s", "Sports", "train")
    ]
    client.force_login(a)
    assert client.get(reverse("review:cin_export"), {"codebook": "V1"}).status_code in (
        302,
        403,
    )


def test_a_training_draw_holds_back_its_share(crawler_schema, db):
    from explorer.models import Article, CandidateLink, Source
    from review.classification import draw_training_batch

    source = Source.objects.using("crawler").create(
        id="s1", host="a.example", canonical_name="A"
    )
    for n in range(400):
        link = CandidateLink.objects.using("crawler").create(
            id=f"cl-{n}",
            url=f"https://a.example/{n}",
            source=source,
            status="extracted",
        )
        Article.objects.using("crawler").create(
            id=f"a-{n}",
            candidate_link=link,
            url=link.url,
            title="T",
            status="labeled",
            text="w",
            primary_label="Education",
            primary_label_confidence=0.9,
        )
    training = open_round(
        "Training",
        Round.COLLECTION,
        base_coders=2,
        finish_rule={"per_category": 250, "holdout": 50},
    )
    cohort, _ = draw_training_batch(training, 2500)
    drawn = cohort.samples.count()
    held = cohort.samples.filter(holdout=True).count()
    assert drawn == 250
    # One in five, give or take what a random share gives.
    assert 30 <= held <= 70
