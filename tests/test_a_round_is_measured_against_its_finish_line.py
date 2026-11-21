"""A round is measured against its finish line, and the result is kept.

Whether new definitions passed their pilot is the decision the whole
programme turns on. The conditions are fixed numbers, "not yet" is never
shown as "no", and an evaluation that has been recorded is never
changed.

See docs/CIN_V2_DATADESK.md, WP8.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from accounts.privileges import ADMIN
from review.classification import (
    GATE_FIRST_PAIR,
    RELIABILITY_TARGET,
    ClassificationCohort,
    ClassificationDecision,
    ClassificationSample,
    ClassificationStory,
    CodebookVersion,
    GateResult,
    Round,
    V1Decision,
    gate,
    hold_review,
    open_round,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def test_the_thresholds_are_the_ones_the_procedure_states():
    """Alpha 0.80 is the condition; 83% is how it reads as a percentage."""
    assert (RELIABILITY_TARGET, GATE_FIRST_PAIR) == (0.80, 0.83)


@pytest.fixture
def coders(db):
    return [
        User.objects.create_user(n, email=f"{n}@localnewsimpact.org") for n in "abc"
    ]


def _pilot(civic, other, base=2):
    """A pilot over V1 stories: `civic` and `other` are how many of each,
    every one coded by V1 with the first two coders disagreeing on the
    civic ones and agreeing on the others."""
    version = CodebookVersion.v1()
    pilot = open_round("Pilot", Round.COMPARISON, base_coders=base)
    cohort = ClassificationCohort.objects.create(
        number=1, slug="cohort-1", round=pilot, codebook_version=version
    )
    for i in range(civic + other):
        is_civic = i < civic
        story = ClassificationStory.objects.create(
            id=f"v1:{i}",
            headline=f"h{i}",
            body="t",
            civic=is_civic,
            v1_outcome=ClassificationStory.NO_LABEL,
        )
        V1Decision.objects.create(
            story=story,
            group="A",
            primary_label="Civic Life" if is_civic else "Sports",
        )
        V1Decision.objects.create(
            story=story,
            group="C",
            primary_label="Civic information" if is_civic else "Sports",
        )
        ClassificationSample.objects.create(
            cohort=cohort,
            article_id=story.id,
            stratum="civic" if is_civic else "other",
        )
    return pilot, cohort


def _code(cohort, story, coders, labels):
    for coder, label in zip(coders, labels, strict=False):
        ClassificationDecision.objects.create(
            cohort=cohort,
            article_id=story,
            decided_by=coder,
            primary_label=label,
            codebook_version=cohort.codebook_version,
        )


def _civic(i):
    """Both civic categories get used, as they would be."""
    return "Civic information" if i % 2 else "Civic Life"


def _by_name(result):
    return {c["name"]: c for c in result["conditions"]}


def _readings(result):
    return {r["name"]: r for r in result["measures"]["first_pair"]}


def test_a_pilot_passes_when_every_alpha_is_met(coders):
    pilot, cohort = _pilot(civic=10, other=10)
    for i in range(10):
        _code(cohort, f"v1:{i}", coders, (_civic(i), _civic(i)))
    for i in range(10, 20):
        _code(cohort, f"v1:{i}", coders, ("Sports", "Sports"))
    result = gate(pilot)
    assert result["outcome"] == GateResult.PASS
    rows = _by_name(result)
    # Three conditions, each an alpha against 0.80.
    assert list(rows) == ["All stories", "Civic information", "Civic Life"]
    overall = rows["All stories"]
    assert overall["value"] == pytest.approx(1.0)
    assert (overall["n"], overall["threshold"]) == (20, 0.80)
    assert overall["low"] == pytest.approx(1.0) == overall["high"]
    assert rows["Civic Life"]["threshold"] == 0.80
    # Beside each, what V1 coders did on the same stories: they split on
    # every civic story, and never chose the same civic category.
    assert overall["v1"] < 0.80 and rows["Civic Life"]["v1"] < 0


def test_percent_agreement_is_shown_and_decides_nothing(coders):
    pilot, cohort = _pilot(civic=10, other=10)
    for i in range(10):
        _code(cohort, f"v1:{i}", coders, (_civic(i), _civic(i)))
    for i in range(10, 20):
        _code(cohort, f"v1:{i}", coders, ("Sports", "Sports"))
    result = gate(pilot)
    rows = _readings(result)
    civic = rows["The civic stories"]
    assert (civic["value"], civic["n"]) == (1.0, 10)
    assert civic["v1"] == 0.0 and rows["The other stories"]["v1"] == 1.0
    assert civic["low"] < 1.0 and civic["high"] == pytest.approx(1.0)
    assert all("outcome" not in row and "threshold" not in row for row in rows.values())
    assert result["measures"]["first_pair_reading"] == 0.83


def test_one_alpha_missed_fails_the_pilot_and_names_it(coders):
    pilot, cohort = _pilot(civic=10, other=10)
    for i in range(10):
        # Coders split on 3 of the 10 civic stories.
        second = "Civic information" if i < 3 else "Civic Life"
        _code(cohort, f"v1:{i}", coders, ("Civic Life", second))
    for i in range(10, 20):
        _code(cohort, f"v1:{i}", coders, ("Sports", "Sports"))
    result = gate(pilot)
    assert result["outcome"] == GateResult.FAIL
    rows = _by_name(result)
    # Civic information: chosen on 3 stories and never matched, which is
    # worse than chance.
    assert rows["Civic information"]["value"] < 0
    assert rows["Civic information"]["outcome"] == GateResult.FAIL
    assert rows["Civic Life"]["outcome"] == GateResult.FAIL
    # 17 of 20 stories matched: 85%, and an alpha of 0.74.
    assert rows["All stories"]["value"] == pytest.approx(0.7406, abs=1e-3)
    assert rows["All stories"]["outcome"] == GateResult.FAIL
    assert _readings(result)["The civic stories"]["value"] == pytest.approx(0.7)


def test_alpha_is_over_every_coder_on_a_story(coders):
    """Three coders read each pilot story. The first two agreeing does
    not make a story agreed where the third chose something else."""
    pilot, cohort = _pilot(civic=10, other=10, base=3)
    for i in range(10):
        _code(cohort, f"v1:{i}", coders, (_civic(i), _civic(i), "Health"))
    for i in range(10, 20):
        _code(cohort, f"v1:{i}", coders, ("Sports", "Sports", "Sports"))
    result = gate(pilot)
    assert _readings(result)["All stories"]["value"] == pytest.approx(1.0)
    assert _by_name(result)["All stories"]["value"] < 0.80
    assert result["outcome"] == GateResult.FAIL


def test_the_all_stories_percentage_counts_civic_at_its_true_share(coders):
    """The pilot holds every civic story and only some of the others, so
    civic stories are a larger share of it than of the V1 stories."""
    pilot, cohort = _pilot(civic=10, other=10)
    # 30 more non-civic V1 stories that are not in the pilot.
    for i in range(100, 130):
        ClassificationStory.objects.create(
            id=f"v1:{i}", v1_outcome=ClassificationStory.LABEL_09
        )
    for i in range(10):
        second = "Civic information" if i < 5 else "Civic Life"
        _code(cohort, f"v1:{i}", coders, ("Civic Life", second))
    for i in range(10, 20):
        _code(cohort, f"v1:{i}", coders, ("Sports", "Sports"))
    overall = _readings(gate(pilot))["All stories"]
    # Civic 50%, other 100%; civic is 10 of 50 kept, so 0.2*0.5 + 0.8*1.
    assert overall["value"] == pytest.approx(0.9)
    assert "20%" in overall["population"]


def test_an_unfinished_pilot_is_not_enough_data_whatever_the_figures(coders):
    pilot, cohort = _pilot(civic=10, other=10)
    for i in range(19):  # one story still has no answers
        label = "Civic Life" if i < 10 else "Sports"
        _code(cohort, f"v1:{i}", coders, (label, label))
    result = gate(pilot)
    assert result["outcome"] == GateResult.NOT_ENOUGH
    assert _by_name(result)["Civic Life"]["outcome"] == GateResult.PASS


def test_a_rejected_pair_is_not_scored_and_a_mixed_pair_is_a_mismatch(coders):
    pilot, cohort = _pilot(civic=0, other=4)
    _code(cohort, "v1:0", coders, ("Sports", "Sports"))
    _code(cohort, "v1:1", coders, ("", ""))  # two rejections: not scored
    _code(cohort, "v1:2", coders, ("Sports", ""))  # mismatch
    _code(cohort, "v1:3", coders, ("Sports", "Sports"))
    result = gate(pilot)
    overall = _readings(result)["All stories"]
    assert overall["n"] == 3 and overall["value"] == pytest.approx(2 / 3)
    # Alpha leaves the twice-rejected story out the same way.
    assert _by_name(result)["All stories"]["n"] == 3


def test_a_recorded_evaluation_keeps_its_figures(coders):
    pilot, cohort = _pilot(civic=10, other=10)
    for i in range(20):
        label = _civic(i) if i < 10 else "Sports"
        _code(cohort, f"v1:{i}", coders, (label, label))
    record = gate(pilot, by=coders[0], record=True)["record"]
    assert record.outcome == GateResult.PASS and record.evaluated_by == coders[0]
    assert len(record.conditions) == 3
    assert len(record.measures["first_pair"]) == 5
    # The change from V1 on the same stories, measured when it is recorded.
    change = record.measures["civic_change"]
    assert change["change"] == pytest.approx(1.0) and change["low"] > 0
    assert record.measures["labelled_share"] == 1.0
    assert record.conditions[0]["value"] == pytest.approx(1.0)

    record.outcome = GateResult.FAIL
    with pytest.raises(ValueError):
        record.save()
    # Evaluating again adds a row and leaves the first alone.
    gate(pilot, by=coders[0], record=True)
    assert GateResult.objects.filter(round=pilot).count() == 2


def test_stories_left_with_no_label_are_counted_by_category(coders):
    pilot, cohort = _pilot(civic=0, other=2)
    _code(cohort, "v1:0", coders, ("Sports", "Health"))
    _code(cohort, "v1:1", coders, ("Sports", "Sports"))
    measures = gate(pilot)["measures"]
    assert measures["left_out"] == {"Sports": 1, "Health": 1}
    assert measures["labelled_share"] == 0.5


def test_a_practice_round_is_finished_by_its_review(coders):
    practice = open_round("Practice", Round.PRACTICE)
    assert gate(practice)["outcome"] == GateResult.NOT_ENOUGH
    hold_review(practice, by=coders[0])
    names = [c["name"] for c in gate(practice)["conditions"]]
    assert names == [
        "Every coder has coded every story",
        "The review has been held",
    ]


def test_a_training_round_passes_when_every_category_has_its_number(coders):
    training = open_round(
        "Training", Round.COLLECTION, base_coders=2, finish_rule={"per_category": 1}
    )
    result = gate(training)
    assert result["outcome"] == GateResult.NOT_ENOUGH
    assert len(result["conditions"]) == 10
    assert result["measures"]["first_pair_floor"] == 0.83


def test_an_admin_records_an_evaluation_from_the_round_page(client, coders):
    admin = User.objects.create_user("admin", password="x")
    Grant.objects.create(user=admin, app=DATADESK, role=ADMIN, scope="")
    client.force_login(admin)
    pilot, cohort = _pilot(civic=10, other=10)
    for i in range(20):
        label = _civic(i) if i < 10 else "Sports"
        _code(cohort, f"v1:{i}", coders, (label, label))
    url = reverse("review:cin_round", args=[pilot.pk])
    body = client.get(url).content.decode()
    assert "Measured against the finish line" in body
    assert "Krippendorff" in body and "0.80" in body
    assert "The civic stories" in body and "83%" in body
    body = client.post(url, {"action": "evaluate"}).content.decode()
    assert "Recorded: pass." in body
    assert GateResult.objects.get().evaluated_by == admin


def test_a_category_nobody_chose_cannot_be_passed(coders):
    """A pilot in which no coder used Civic Information has no evidence
    about Civic Information. That is not enough data, not a pass."""
    pilot, cohort = _pilot(civic=10, other=10)
    for i in range(20):
        label = "Civic Life" if i < 10 else "Sports"
        _code(cohort, f"v1:{i}", coders, (label, label))
    result = gate(pilot)
    assert _by_name(result)["Civic information"]["value"] is None
    assert result["outcome"] == GateResult.NOT_ENOUGH
