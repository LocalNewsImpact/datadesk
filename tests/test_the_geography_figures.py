"""Every geography ICR figure, against a case worked by hand.

The arithmetic (`review.geography_metrics`) has no database in it, so it
is checked on numbers small enough to do on paper. The report
(`geography_icr.geography_report`) is then run over a small round whose
answers and saved claims are chosen so each figure has a known value
(docs/GEOGRAPHY_ICR_DATADESK.md, G-WP7).
"""

import random

import pytest
from django.contrib.auth.models import User

from review import cin_metrics
from review import geography_metrics as gm
from review.classification import (
    ClassificationCohort,
    ClassificationSample,
    Round,
    open_round,
)
from review.geography_icr import (
    GEOGRAPHY,
    NO_PLACE,
    PLACE,
    REGION,
    STATE,
    UNSTATED,
    GeographyClaim,
    GeographyDecision,
    clean_answer,
    geography_report,
    pipeline_where,
)

# --------------------------------------------------------- the arithmetic


def test_nominal_alpha_by_hand():
    """Values 1 x3 and 2 x3, n = 6. One unit disagrees: observed 2.
    Expected t.D.t = 2 * 3 * 3 = 18. alpha = 1 - 5 * 2 / 18 = 4/9."""
    assert gm.alpha([[1, 1], [2, 2], [1, 2]]) == pytest.approx(4 / 9)


def test_nominal_alpha_is_the_cin_figure():
    rng = random.Random(1)
    stories = {
        i: [{"label": rng.choice("ABCD")} for _ in range(rng.choice([2, 3]))]
        for i in range(200)
    }
    units = [[a["label"] for a in answers] for answers in stories.values()]
    assert gm.alpha(units) == pytest.approx(cin_metrics.krippendorff_alpha(stories))


def test_masi_by_hand():
    assert gm.masi({1, 2}, {1, 2}) == 0
    assert gm.masi({1, 2}, {1}) == pytest.approx(1 - 0.5 * 2 / 3)
    assert gm.masi({1, 2}, {2, 3}) == pytest.approx(1 - (1 / 3) * (1 / 3))
    assert gm.masi({1}, {2}) == 1


def test_masi_alpha_by_hand():
    """{a} x3, {a,b} x1, n = 4; d({a}, {a,b}) = 2/3. Observed: the second
    unit, 2 * 2/3 = 4/3. Expected 2 * 3 * 1 * 2/3 = 4. alpha = 1 - 3 *
    (4/3) / 4 = 0."""
    a, ab = frozenset("a"), frozenset("ab")
    assert gm.alpha([[a, a], [ab, a]], gm.masi) == pytest.approx(0)


def test_alpha_needs_variation_and_pairs():
    assert gm.alpha([[1, 1], [1, 1]]) is None
    assert gm.alpha([[1], [2]]) is None
    assert gm.alpha_with_interval([[1, 1]])["rate"] is None


def test_alpha_interval_holds_the_figure_and_repeats():
    rng = random.Random(2)
    units = [[rng.choice("AB") for _ in range(2)] for _ in range(80)]
    first = gm.alpha_with_interval(units, resamples=200, seed=4)
    assert first["low"] <= first["rate"] <= first["high"] and first["n"] == 80
    assert gm.alpha_with_interval(units, resamples=200, seed=4) == first


def test_a_weighted_share_counts_each_story_for_its_weight():
    share = gm.weighted_share([(True, 1), (False, 3)], resamples=200)
    assert share["rate"] == pytest.approx(0.25)
    assert share["n"] == 2 and share["hits"] == 1
    assert gm.weighted_share([])["rate"] is None


def test_a_pooled_ratio_by_hand():
    """(1 of 2 at weight 1) and (2 of 2 at weight 3): 7 / 8."""
    ratio = gm.weighted_ratio([(1, 2, 1), (2, 2, 3), (0, 0, 9)], resamples=200)
    assert ratio["rate"] == pytest.approx(7 / 8) and ratio["n"] == 2


def test_brier_by_hand():
    result = gm.brier([(0.9, True, 1), (0.9, False, 1), (None, True, 1)])
    assert result["score"] == pytest.approx((0.01 + 0.81) / 2)
    assert result["rate"] == 0.5 and result["constant"] == 0.25
    assert result["n"] == 2
    (row,) = result["table"]
    assert row["from"] == 0.9 and row["forecast"] == pytest.approx(0.9)
    assert row["observed"] == 0.5


def test_distances():
    km = gm.haversine_km(38.9517, -92.3341, 38.5767, -92.1735)
    assert 40 < km < 46  # Columbia to Jefferson City
    assert gm.percentile([1, 2, 3, 4], 50) == 2.5
    assert gm.spread([]) == {"n": 0, "median": None, "p90": None}


# --------------------------------------------------------------- a round

pytestmark_db = pytest.mark.django_db(databases=["default", "crawler"])

COLUMBIA = {"lat": 38.9517, "lon": -92.3341, "view_km": 20}
COLUMBIA_EAST = {"lat": 38.9530, "lon": -92.3000, "view_km": 20}
RURAL_BOONE = {"lat": 38.70, "lon": -92.23, "view_km": 20}
JEFFERSON_CITY = {"lat": 38.5767, "lon": -92.1735, "view_km": 20}


def _claim(round, story, kind, **fields):
    return GeographyClaim.objects.create(
        round=round, article_id=story, pipeline_class=kind, **fields
    )


def _answers(cohort, story, *answers):
    pairs = zip(_answers.coders, answers, strict=True)
    for user, (kind, pins, counties, state) in pairs:
        GeographyDecision.objects.create(
            cohort=cohort,
            article_id=story,
            decided_by=user,
            codebook_version=cohort.codebook_version,
            **clean_answer(kind, pins, counties, state),
        )


@pytest.fixture
def scored(db):
    """Six stories, two coders each, chosen so every figure is known.

    s1 P: coders in Columbia; pipeline Columbia (place)      hit
    s2 P: coders in Columbia; pipeline Jefferson City         miss
    s3 R: coders Boone+Cole; pipeline Boone+Callaway         P 1/2, R 1/2
    s4 S: coders Missouri; pipeline statewide 29             match
    s5 U: coders cannot tell; pipeline assumed a city         unplaceable
    s6 N: coders say no place; pipeline said P, confident     class miss
    """
    pilot = open_round("Geo pilot", Round.COMPARISON, task=GEOGRAPHY, base_coders=2)
    cohort = ClassificationCohort.objects.create(
        number=1, slug="cohort-1", round=pilot, codebook_version=pilot.codebook_version
    )
    _answers.coders = [User.objects.create_user(n) for n in "ab"]
    weights = {"s1": 0.5, "s2": 0.25, "s3": 1, "s4": 1, "s5": 1, "s6": 1}
    for story, p in weights.items():
        ClassificationSample.objects.create(
            cohort=cohort, article_id=story, stratum="p_model", inclusion_probability=p
        )
    _claim(
        pilot,
        "s1",
        PLACE,
        point_geoid="2915670",
        point_geoid_level="place",
        point_lat=38.95,
        point_lon=-92.33,
        point_method="focus_model",
        point_support="named",
        point_place="Columbia",
        scope_confidence=0.9,
    )
    _claim(
        pilot,
        "s2",
        PLACE,
        point_geoid="2937000",
        point_geoid_level="place",
        point_lat=38.5767,
        point_lon=-92.1735,
        point_method="publication_city",
        point_place="Jefferson City",
        scope_confidence=0.8,
    )
    _claim(
        pilot,
        "s3",
        REGION,
        scope_confidence=0.7,
        geoid_rows=[
            {"geoid": "29019", "level": "county", "source": "mention"},
            {"geoid": "29027", "level": "county", "source": "county_rollup"},
        ],
    )
    _claim(
        pilot,
        "s4",
        STATE,
        point_geoid="29",
        point_geoid_level="state",
        scope_confidence=1.0,
    )
    _claim(
        pilot,
        "s5",
        PLACE,
        point_geoid="2915670",
        point_geoid_level="place",
        point_method="publication_place_assumed",
    )
    _claim(pilot, "s6", PLACE, scope_confidence=0.95)
    _answers(
        cohort,
        "s1",
        (PLACE, [COLUMBIA], [], ""),
        (PLACE, [COLUMBIA_EAST], [], ""),
    )
    _answers(
        cohort, "s2", (PLACE, [COLUMBIA], [], ""), (PLACE, [COLUMBIA_EAST], [], "")
    )
    _answers(
        cohort,
        "s3",
        (REGION, [], ["29019", "29051"], ""),
        (REGION, [], ["29019", "29051"], ""),
    )
    _answers(cohort, "s4", (STATE, [], [], "29"), (STATE, [], [], "29"))
    _answers(cohort, "s5", (UNSTATED, [], [], ""), (UNSTATED, [], [], ""))
    _answers(cohort, "s6", (NO_PLACE, [], [], ""), (NO_PLACE, [], [], ""))
    return pilot


@pytestmark_db
def test_the_report_over_a_round_worked_by_hand(scored):
    report = geography_report(scored, resamples=100)

    assert report["counts"]["answered"] == 6 and report["counts"]["consensus"] == 6
    # Every pair agreed on class: alpha 1.
    assert report["m1_class_alpha"]["overall"]["rate"] == pytest.approx(1)
    assert report["m4_coder_distance"]["n"] == 2

    # M5: s1 hit at weight 2, s2 miss at weight 4.
    place = report["m5_place_accuracy"]
    assert place["overall"]["n"] == 2 and place["overall"]["hits"] == 1
    assert place["overall"]["rate"] == pytest.approx(2 / 6)
    assert place["by_method"]["focus_model"]["rate"] == 1
    assert place["by_method"]["publication_city"]["rate"] == 0

    # M6: Boone in common. Precision 1 of 2, recall 1 of 2; the mention
    # was right and the rolled-up county was not.
    region = report["m6_region"]
    assert region["precision"]["rate"] == 0.5 and region["recall"]["rate"] == 0.5
    assert region["exact"]["rate"] == 0
    assert region["precision_by_source"]["mention"]["rate"] == 1
    assert region["precision_by_source"]["county_rollup"]["rate"] == 0

    # M7: pipeline class by the coders' class.
    confusion = report["m7_confusion"]
    assert confusion[PLACE][PLACE]["n"] == 2 and confusion[PLACE][NO_PLACE]["n"] == 1
    assert confusion[PLACE][UNSTATED]["n"] == 1

    # M8: the miss is about 43 km out.
    assert (
        35
        < report["m8_pipeline_distance"]["by_method"]["publication_city"]["median"]
        < 50
    )

    # M9: s1-s4 right class, s6 wrong at 0.95; s5 (U) left out.
    calibration = report["m9_scope_calibration"]
    assert calibration["n"] == 5
    assert calibration["score"] == pytest.approx(
        (2 * 0.01 + 4 * 0.04 + 0.09 + 0 + 0.95**2) / (2 + 4 + 1 + 1 + 1)
    )

    # M10: s1 (Boone = Boone) and s4 (state 29) match; s2's county is
    # Cole not Boone; s3's sets differ; s6 has no point and the coders
    # said no place, so both credit nothing.
    end = report["m10_end_to_end"]["overall"]
    assert end["n"] == 5 and end["hits"] == 3

    assert report["m11_unplaceable"] == {"publication_place_assumed": 1}


@pytestmark_db
def test_a_county_point_is_compared_at_county_level(scored):
    claim = GeographyClaim(point_geoid="29019", point_geoid_level="county")
    assert pipeline_where(claim) == (None, "29019")
    assert pipeline_where(GeographyClaim()) == (None, None)
    # A place point with no coordinates falls back to the crosswalk.
    place = GeographyClaim(point_geoid="2915670", point_geoid_level="place")
    assert pipeline_where(place) == ("2915670", "29019")
