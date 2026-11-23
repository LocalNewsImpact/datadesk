"""A geography round is judged against its gates, and an admin can see
why (docs/GEOGRAPHY_ICR_DATADESK.md, G-WP8 and G-WP9).

Gate 1 asks whether coders agree with each other; until it passes, the
pipeline is not judged at all. Gates 2 and 3 ask whether the pipeline is
right often enough, on the lower end of the interval. A round still being
coded is "not enough data" however its figures look.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from accounts.privileges import ADMIN, CLASSIFIER
from review.classification import (
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationSample,
    CoderStanding,
    GateResult,
    Round,
    open_round,
    standings,
)
from review.geography_icr import (
    GEOGRAPHY,
    NO_PLACE,
    PLACE,
    REGION,
    STATE,
    GeographyClaim,
    GeographyDecision,
    clean_answer,
    coder_figures,
    disagreements,
    geography_gate,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

#: Ten towns in ten counties, so the county answers vary.
TOWNS = [
    (38.9517, -92.3341, "29019"),  # Columbia, Boone
    (38.5767, -92.1735, "29051"),  # Jefferson City, Cole
    (39.0997, -94.5786, "29095"),  # Kansas City, Jackson
    (37.2089, -93.2923, "29077"),  # Springfield, Greene
    (37.0842, -94.5133, "29097"),  # Joplin, Jasper
    (39.7675, -94.8467, "29021"),  # St. Joseph, Buchanan
    (36.7570, -90.3929, "29023"),  # Poplar Bluff, Butler
    (38.7045, -93.2283, "29159"),  # Sedalia, Pettis
    (39.1689, -91.8829, "29007"),  # Mexico, Audrain
    (38.6270, -90.1990, "29510"),  # St. Louis
]
REGIONS = [
    ["29019", "29051"],
    ["29095"],
    ["29077", "29097", "29043"],
    ["29021"],
    ["29510", "29189"],
]


def _pin(town):
    lat, lon, _ = town
    return {"lat": lat, "lon": lon, "view_km": 20}


class Pilot:
    """A geography pilot built story by story: each story's two answers
    and the pipeline's claim."""

    def __init__(self, name="Geo pilot"):
        self.round = open_round(
            name, Round.COMPARISON, task=GEOGRAPHY, base_coders=2, third_on_split=True
        )
        self.cohort = ClassificationCohort.objects.create(
            number=ClassificationCohort.objects.count() + 1,
            slug=f"cohort-{ClassificationCohort.objects.count() + 1}",
            round=self.round,
            codebook_version=self.round.codebook_version,
            target_coders=2,
        )
        self.coders = [User.objects.get_or_create(username=n)[0] for n in ("ann", "bo")]
        self.n = 0

    def story(self, first, second=None, claim=None, *, answered=True):
        self.n += 1
        story = f"{self.round.pk}-s{self.n}"
        ClassificationSample.objects.create(
            cohort=self.cohort, article_id=story, stratum="p_model"
        )
        if claim is not None:
            GeographyClaim.objects.create(round=self.round, article_id=story, **claim)
        if answered:
            for user, answer in zip(self.coders, (first, second or first), strict=True):
                GeographyDecision.objects.create(
                    cohort=self.cohort,
                    article_id=story,
                    decided_by=user,
                    codebook_version=self.round.codebook_version,
                    **clean_answer(*answer),
                )
        return story


def _place_claim(town):
    lat, lon, _ = town
    return {
        "pipeline_class": PLACE,
        "point_geoid": "x",
        "point_geoid_level": "place",
        "point_lat": lat,
        "point_lon": lon,
        "point_method": "focus_model",
        "scope_confidence": 0.9,
    }


def _region_claim(counties):
    return {
        "pipeline_class": REGION,
        "geoid_rows": [
            {"geoid": c, "level": "county", "source": "mention"} for c in counties
        ],
        "scope_confidence": 0.9,
    }


def _agreeing_pilot(*, place_right=True):
    """Coders agree on everything, with variety; the pipeline is right
    on every region and, if `place_right`, every place."""
    pilot = Pilot()
    for i, town in enumerate(TOWNS):
        claimed = town if place_right or i % 2 else TOWNS[(i + 3) % len(TOWNS)]
        pilot.story((PLACE, [_pin(town)]), claim=_place_claim(claimed))
    for counties in REGIONS:
        pilot.story((REGION, [], counties), claim=_region_claim(counties))
    pilot.story((STATE, [], [], "29"), claim={"pipeline_class": STATE})
    pilot.story((NO_PLACE,), claim={"pipeline_class": NO_PLACE})
    return pilot


def _outcomes(result):
    return {row["name"]: row["outcome"] for row in result["conditions"]}


# ------------------------------------------------------------- the gates


def test_a_pilot_where_everyone_agrees_and_the_pipeline_is_right_passes():
    pilot = _agreeing_pilot()
    result = geography_gate(pilot.round)
    assert set(_outcomes(result).values()) == {GateResult.PASS}
    assert result["outcome"] == GateResult.PASS
    gates = {row["gate"] for row in result["conditions"]}
    assert gates == {1, 2, 3}


def test_a_pipeline_wrong_on_half_the_places_fails_gate_two():
    pilot = _agreeing_pilot(place_right=False)
    result = geography_gate(pilot.round)
    place = next(row for row in result["conditions"] if row["gate"] == 2)
    assert place["outcome"] == GateResult.FAIL
    assert place["low"] < 0.90 and place["on"] == "lower bound"
    assert result["outcome"] == GateResult.FAIL


def test_until_coders_agree_the_pipeline_is_not_judged():
    pilot = Pilot()
    classes = [
        ((PLACE, [_pin(TOWNS[0])]), (REGION, [], ["29019"])),
        ((STATE, [], [], "29"), (NO_PLACE,)),
        ((NO_PLACE,), (PLACE, [_pin(TOWNS[1])])),
        ((REGION, [], ["29095"]), (STATE, [], [], "29")),
    ] * 3
    for first, second in classes:
        pilot.story(first, second, claim=_place_claim(TOWNS[0]))
    for town in TOWNS[:4]:
        pilot.story((PLACE, [_pin(town)]), claim=_place_claim(town))
    result = geography_gate(pilot.round)
    first = next(row for row in result["conditions"] if row["gate"] == 1)
    assert first["outcome"] == GateResult.FAIL
    held = [row for row in result["conditions"] if row["gate"] in (2, 3)]
    assert all(row["outcome"] == GateResult.NOT_ENOUGH for row in held)
    assert all(row["held"] == "gate 1 has not passed" for row in held)
    # The pipeline's figures are still shown, only not judged.
    assert held[0]["value"] is not None
    # Splits are waiting on their thirds: not enough, not fail.
    assert result["outcome"] == GateResult.NOT_ENOUGH


def test_a_round_still_being_coded_is_not_enough_however_it_looks():
    pilot = _agreeing_pilot()
    pilot.story((PLACE, [_pin(TOWNS[0])]), answered=False)
    assert geography_gate(pilot.round)["outcome"] == GateResult.NOT_ENOUGH


def test_an_evaluation_is_recorded_with_its_figures():
    pilot = _agreeing_pilot()
    admin = User.objects.create_user("boss")
    result = geography_gate(pilot.round, by=admin, record=True)
    record = result["record"]
    assert record.outcome == GateResult.PASS and record.evaluated_by == admin
    assert len(record.conditions) == 6
    assert record.measures["m5_place_accuracy"]["overall"]["rate"] == 1
    # Kept as JSON: a bool cut key becomes a string.
    assert "False" in record.measures["m5_place_accuracy"]["by_better_known"]
    with pytest.raises(ValueError):
        record.save()


def test_a_geography_practice_passes_when_coded_and_reviewed():
    from review.classification import hold_review

    practice = open_round("Geo practice", Round.PRACTICE, task=GEOGRAPHY)
    cohort = ClassificationCohort.objects.create(
        number=50,
        slug="cohort-50",
        round=practice,
        codebook_version=practice.codebook_version,
    )
    coder = User.objects.create_user("cara")
    Grant.objects.create(user=coder, app=DATADESK, scope=cohort.slug, role=CLASSIFIER)
    ClassificationAssignment.objects.create(
        cohort=cohort, article_id="p1", assigned_to=coder
    )
    assert geography_gate(practice)["outcome"] == GateResult.NOT_ENOUGH
    ClassificationAssignment.objects.filter(assigned_to=coder).update(
        completed_at="2026-11-23T00:00:00Z"
    )
    admitted = hold_review(practice, by=None)
    assert admitted == [coder]
    assert standings(practice.codebook_version)[coder.id] == CoderStanding.ADMITTED
    assert geography_gate(practice)["outcome"] == GateResult.PASS


# --------------------------------------------------------- what an admin sees


def test_coder_figures_read_first_pairs():
    pilot = Pilot()
    pilot.story((PLACE, [_pin(TOWNS[0])]))
    pilot.story((PLACE, [_pin(TOWNS[0])]), (PLACE, [_pin(TOWNS[1])]))
    figures = coder_figures(pilot.round)
    for coder in pilot.coders:
        assert figures[coder.id]["agreement"] == {"rate": 0.5, "n": 2}


def test_disagreements_list_the_splits_with_every_answer_and_the_claim(crawler_schema):
    pilot = Pilot()
    pilot.story((PLACE, [_pin(TOWNS[0])]), claim=_place_claim(TOWNS[0]))
    split = pilot.story(
        (PLACE, [_pin(TOWNS[0])]),
        (REGION, [], ["29019", "29051"]),
        claim=_region_claim(["29019"]),
    )
    rows = disagreements(pilot.round)
    assert [row["article_id"] for row in rows] == [split]
    (row,) = rows
    assert [a["coder"] for a in row["answers"]] == ["ann", "bo"]
    assert "Columbia, Boone County" in row["answers"][0]["said"]
    assert "Boone" in row["answers"][1]["said"] and "Cole" in row["answers"][1]["said"]
    assert row["pipeline"]["counties"] == ["29019"]
    assert len(disagreements(pilot.round, include_agreed=True)) == 2


def test_a_state_answer_reads_as_the_state(crawler_schema):
    pilot = Pilot()
    pilot.story((STATE, [], [], "29"), (NO_PLACE,))
    (row,) = disagreements(pilot.round)
    assert row["answers"][0]["said"] == "The whole state: Missouri"


@pytest.fixture
def admin(db):
    user = User.objects.create_user("boss", password="x")
    Grant.objects.create(user=user, app=DATADESK, role=ADMIN, scope="")
    return user


def test_the_round_page_shows_the_gates_and_figures(client, admin):
    pilot = _agreeing_pilot()
    client.force_login(admin)
    url = reverse("review:cin_round", args=[pilot.round.pk])
    body = client.get(url).content.decode()
    assert "Measured against the gates" in body
    assert "The pipeline places a single place right" in body
    assert "M5 single places right" in body and "M9 scope confidence" in body
    assert reverse("review:geography_disagreements", args=[pilot.round.pk]) in body
    # None of the CIN pilot's furniture.
    assert "Labels for the retrain" not in body and "The lead's decision" not in body


def test_recording_an_evaluation_from_the_round_page(client, admin):
    pilot = _agreeing_pilot()
    client.force_login(admin)
    client.post(
        reverse("review:cin_round", args=[pilot.round.pk]), {"action": "evaluate"}
    )
    record = GateResult.objects.get(round=pilot.round)
    assert record.outcome == GateResult.PASS and record.evaluated_by == admin


def test_a_geography_practice_page_offers_the_review(client, admin):
    practice = open_round("Geo practice", Round.PRACTICE, task=GEOGRAPHY)
    client.force_login(admin)
    body = client.get(reverse("review:cin_round", args=[practice.pk])).content.decode()
    assert "Practice review" in body and 'value="review"' in body
    assert "Measured against the gates" not in body


def test_the_disagreements_page_is_for_admins(client, admin, crawler_schema):
    pilot = Pilot()
    pilot.story(
        (PLACE, [_pin(TOWNS[0])]),
        (PLACE, [_pin(TOWNS[1])]),
        claim=_place_claim(TOWNS[2]),
    )
    url = reverse("review:geography_disagreements", args=[pilot.round.pk])
    client.force_login(admin)
    body = client.get(url).content.decode()
    assert "Where coders disagreed" in body and "geo-dis-data" in body
    assert "ann:" in body and "bo:" in body and "Pipeline:" in body

    coder = User.objects.create_user("coder")
    Grant.objects.create(
        user=coder, app=DATADESK, scope=pilot.cohort.slug, role=CLASSIFIER
    )
    client.force_login(coder)
    assert client.get(url).status_code in (302, 403)


def test_a_cin_round_has_no_disagreements_page(client, admin):
    cin = open_round("CIN pilot", Round.COMPARISON)
    client.force_login(admin)
    url = reverse("review:geography_disagreements", args=[cin.pk])
    assert client.get(url).status_code == 404
