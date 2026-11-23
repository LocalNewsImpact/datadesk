"""Two coders agree on where a story is, or a third decides.

Agreement is on GEOIDs at the coarser of the two answers' levels: two
pins in one town agree; a pin in a town and one in the countryside
around it agree on the county; pins in two towns do not. A split in a
pilot gets one more coder, and the story's answer is what at least two
chose (docs/GEOGRAPHY_ICR_DATADESK.md, G-WP6).
"""

import json

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from accounts.privileges import CLASSIFIER
from review.classification import (
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationSample,
    CoderStanding,
    Round,
    open_round,
    set_standing,
)
from review.geography_icr import (
    GEOGRAPHY,
    NO_PLACE,
    PLACE,
    REGION,
    STATE,
    UNSTATED,
    GeographyDecision,
    add_third_coder,
    agree,
    clean_answer,
    consensus,
    round_consensus,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

COLUMBIA = {"lat": 38.9517, "lon": -92.3341, "view_km": 20}
COLUMBIA_EAST = {"lat": 38.9530, "lon": -92.3000, "view_km": 20}
RURAL_BOONE = {"lat": 38.70, "lon": -92.23, "view_km": 20}
ASHLAND = {"lat": 38.7745, "lon": -92.2571, "view_km": 20}
JEFFERSON_CITY = {"lat": 38.5767, "lon": -92.1735, "view_km": 20}


def _answer(kind, pins=(), counties=(), state=""):
    """An unsaved decision carrying a cleaned answer."""
    return GeographyDecision(**clean_answer(kind, list(pins), list(counties), state))


# ------------------------------------------------------------ agreement


@pytest.mark.parametrize(
    "a,b,agreed",
    [
        # Two pins in one town, metres apart.
        ((PLACE, [COLUMBIA]), (PLACE, [COLUMBIA_EAST]), True),
        # A town and the countryside around it: the county.
        ((PLACE, [COLUMBIA]), (PLACE, [RURAL_BOONE]), True),
        # Two towns in one county: both answered at place level.
        ((PLACE, [COLUMBIA]), (PLACE, [ASHLAND]), False),
        # Different counties.
        ((PLACE, [RURAL_BOONE]), (PLACE, [JEFFERSON_CITY]), False),
        # Different classes never agree, even on the same county.
        ((PLACE, [COLUMBIA]), (REGION, [], ["29019"]), False),
        ((REGION, [], ["29019", "29051"]), (REGION, [JEFFERSON_CITY], ["29019"]), True),
        ((REGION, [], ["29019", "29051"]), (REGION, [], ["29019"]), False),
        ((STATE, [], [], "29"), (STATE, [], [], "29"), True),
        ((STATE, [], [], "29"), (STATE, [], [], "20"), False),
        ((UNSTATED,), (UNSTATED,), True),
        ((NO_PLACE,), (UNSTATED,), False),
    ],
)
def test_two_answers_agree_at_the_coarser_level(a, b, agreed):
    assert agree(_answer(*a), _answer(*b)) is agreed


# ------------------------------------------------------------ consensus


def test_a_place_two_chose_is_the_story_place():
    result = consensus(
        [
            _answer(PLACE, [COLUMBIA]),
            _answer(PLACE, [ASHLAND]),
            _answer(PLACE, [COLUMBIA_EAST]),
        ]
    )
    assert result["level"] == "place" and result["place"] == "2915670"
    assert result["counties"] == ["29019"] and result["coders"] == 3


def test_two_places_in_one_county_and_no_third_town_give_the_county():
    result = consensus(
        [
            _answer(PLACE, [COLUMBIA]),
            _answer(PLACE, [ASHLAND]),
            _answer(PLACE, [JEFFERSON_CITY]),
        ]
    )
    assert result["level"] == "county" and result["place"] is None
    assert result["counties"] == ["29019"]


def test_a_town_and_its_countryside_give_the_county():
    result = consensus([_answer(PLACE, [COLUMBIA]), _answer(PLACE, [RURAL_BOONE])])
    assert result["level"] == "county" and result["counties"] == ["29019"]


def test_three_counties_three_ways_is_no_consensus():
    assert (
        consensus(
            [
                _answer(PLACE, [RURAL_BOONE]),
                _answer(PLACE, [JEFFERSON_CITY]),
                _answer(PLACE, [{"lat": 39.0997, "lon": -94.5786, "view_km": 20}]),
            ]
        )
        is None
    )


def test_three_classes_three_ways_is_no_consensus():
    assert (
        consensus(
            [_answer(PLACE, [COLUMBIA]), _answer(STATE, state="29"), _answer(NO_PLACE)]
        )
        is None
    )


def test_the_class_two_chose_wins_and_the_third_answer_is_set_aside():
    result = consensus(
        [
            _answer(REGION, [], ["29019", "29051"]),
            _answer(REGION, [], ["29019", "29027"]),
            _answer(PLACE, [COLUMBIA]),
        ]
    )
    assert result["answer_class"] == REGION
    # Only the county both regional answers chose.
    assert result["counties"] == ["29019"]


def test_two_regions_with_no_county_in_common_are_no_consensus():
    assert (
        consensus([_answer(REGION, [], ["29019"]), _answer(REGION, [], ["29051"])])
        is None
    )


def test_a_state_and_the_no_place_classes():
    assert consensus([_answer(STATE, state="29")] * 2)["state"] == "29"
    assert consensus([_answer(UNSTATED)] * 2)["answer_class"] == UNSTATED
    assert consensus([_answer(NO_PLACE)]) is None


# ------------------------------------------------------- the third coder


@pytest.fixture
def pilot(db):
    return open_round(
        "Geo pilot",
        Round.COMPARISON,
        task=GEOGRAPHY,
        base_coders=2,
        third_on_split=True,
    )


@pytest.fixture
def coders(pilot):
    users = [User.objects.create_user(n, email=f"{n}@x.org") for n in "abcd"]
    for user in users:
        set_standing(user, pilot.codebook_version, CoderStanding.ADMITTED)
    return users


@pytest.fixture
def cohort(pilot, coders):
    cohort = ClassificationCohort.objects.create(
        number=1,
        slug="cohort-1",
        round=pilot,
        codebook_version=pilot.codebook_version,
        target_coders=2,
    )
    for user in coders:
        Grant.objects.create(
            user=user, app=DATADESK, scope=cohort.slug, role=CLASSIFIER
        )
    for story in ("a-1", "a-2"):
        ClassificationSample.objects.create(
            cohort=cohort, article_id=story, stratum="p_model"
        )
        for user in coders[:2]:
            ClassificationAssignment.objects.create(
                cohort=cohort, article_id=story, assigned_to=user
            )
    return cohort


def _save(cohort, user, story, kind, pins=(), counties=(), state=""):
    return GeographyDecision.objects.create(
        cohort=cohort,
        article_id=story,
        decided_by=user,
        codebook_version=cohort.codebook_version,
        **clean_answer(kind, list(pins), list(counties), state),
    )


def test_a_split_gets_one_third_coder_who_has_not_seen_the_story(cohort, coders):
    _save(cohort, coders[0], "a-1", PLACE, [COLUMBIA])
    assert add_third_coder(cohort, "a-1") is None  # one answer: nothing yet
    _save(cohort, coders[1], "a-1", PLACE, [JEFFERSON_CITY])
    third = add_third_coder(cohort, "a-1")
    assert third is not None and third.assigned_to in coders[2:]
    # Called again: still three on the story.
    assert add_third_coder(cohort, "a-1") is None
    assert ClassificationAssignment.objects.filter(article_id="a-1").count() == 3


def test_an_agreed_story_gets_no_third(cohort, coders):
    _save(cohort, coders[0], "a-1", PLACE, [COLUMBIA])
    _save(cohort, coders[1], "a-1", PLACE, [RURAL_BOONE])
    assert add_third_coder(cohort, "a-1") is None


def test_no_third_coder_in_practice_or_where_the_round_does_not_ask(coders):
    practice = open_round("Geo practice", Round.PRACTICE, task=GEOGRAPHY)
    plain = open_round("Geo plain", Round.COMPARISON, task=GEOGRAPHY, base_coders=2)
    for n, for_round in enumerate((practice, plain), start=10):
        cohort = ClassificationCohort.objects.create(
            number=n,
            slug=f"cohort-{n}",
            round=for_round,
            codebook_version=for_round.codebook_version,
        )
        _save(cohort, coders[0], f"x-{n}", PLACE, [COLUMBIA])
        _save(cohort, coders[1], f"x-{n}", PLACE, [JEFFERSON_CITY])
        assert add_third_coder(cohort, f"x-{n}") is None


def test_saving_a_split_in_the_queue_adds_the_third(client, cohort, coders):
    _save(cohort, coders[0], "a-1", PLACE, [COLUMBIA])
    assignment = ClassificationAssignment.objects.get(
        article_id="a-1", assigned_to=coders[1]
    )
    client.force_login(coders[1])
    client.post(
        "/review/classification/geography/",
        {
            "assignment": assignment.pk,
            "answer_class": PLACE,
            "pins": json.dumps([JEFFERSON_CITY]),
        },
    )
    assert ClassificationAssignment.objects.filter(article_id="a-1").count() == 3


def test_a_started_geography_pilot_asks_for_thirds(crawler_schema):
    from review.classification import start_round

    created, _ = start_round("Geo pilot", Round.COMPARISON, [], task=GEOGRAPHY)
    assert created.third_on_split
    cin, _ = start_round("CIN pilot", Round.COMPARISON, [])
    assert not cin.third_on_split


# ------------------------------------------------------- the round's view


def test_the_round_counts_agreement_splits_thirds_and_consensus(cohort, coders):
    # a-1: the first two agree on the county.
    _save(cohort, coders[0], "a-1", PLACE, [COLUMBIA])
    _save(cohort, coders[1], "a-1", PLACE, [RURAL_BOONE])
    # a-2: split, then a third who sides with the first.
    _save(cohort, coders[0], "a-2", REGION, [], ["29019", "29051"])
    _save(cohort, coders[1], "a-2", STATE, state="29")
    _save(cohort, coders[2], "a-2", REGION, [], ["29019"])
    # a-3: split, third not in yet.
    _save(cohort, coders[0], "a-3", NO_PLACE)
    _save(cohort, coders[1], "a-3", UNSTATED)
    # a-4: one answer only.
    _save(cohort, coders[0], "a-4", NO_PLACE)

    results, counts = round_consensus(cohort.round)
    assert counts == {
        "answered": 3,
        "agreed": 1,
        "split": 2,
        "third": 1,
        "waiting": 1,
        "consensus": 2,
        "none": 0,
    }
    assert results["a-1"]["counties"] == ["29019"]
    assert results["a-2"]["answer_class"] == REGION
    assert results["a-2"]["counties"] == ["29019"]
    assert "a-3" not in results and "a-4" not in results


def test_a_removed_coders_answers_do_not_count(cohort, coders):
    _save(cohort, coders[0], "a-1", PLACE, [COLUMBIA])
    _save(cohort, coders[1], "a-1", PLACE, [COLUMBIA_EAST])
    set_standing(coders[1], cohort.codebook_version, CoderStanding.REMOVED)
    results, counts = round_consensus(cohort.round)
    assert counts["answered"] == 0 and results == {}
