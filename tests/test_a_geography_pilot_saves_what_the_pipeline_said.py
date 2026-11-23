"""A geography pilot is drawn by how the pipeline placed each story, and
keeps a copy of that placement.

The crawler rewrites a story's geography on every enrichment run, so the
claim the coders are scored against is copied when the story is drawn
and read from the copy ever after (docs/GEOGRAPHY_ICR_DATADESK.md,
G-WP5). The strata oversample the doubtful kinds of placement, and each
story carries its inclusion probability so a rate can be weighted back
to the corpus.
"""

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from accounts.privileges import CLASSIFIER
from review.classification import (
    ClassificationAssignment,
    ClassificationCohort,
    ClassificationSample,
    CodebookVersion,
    CoderStanding,
    Round,
    assign_cohort,
    open_batch,
    open_round,
    set_standing,
)
from review.geography_icr import (
    GEOGRAPHY,
    N_NONE,
    P_ASSUMED,
    P_COUNTY,
    P_INSTITUTION,
    P_MODEL,
    P_NONE,
    P_PUBCITY,
    R_MANY,
    R_NONE,
    R_ONE,
    S_STATE,
    GeographyClaim,
    _wanted,
    draw_geography_pilot,
    stratum_of,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


# ------------------------------------------------------------ the strata


@pytest.mark.parametrize(
    "claim,stratum",
    [
        (("city_municipality", "29019", "county", "focus_model", "named"), P_COUNTY),
        # County level wins over every other test.
        (
            ("city_municipality", "29019", "county", "publication_city", "x"),
            P_COUNTY,
        ),
        (
            ("city_municipality", "2915670", "place", "publication_place_assumed", ""),
            P_ASSUMED,
        ),
        (
            ("neighborhood_community", "2915670", "place", "single_city", "named"),
            P_PUBCITY,
        ),
        # A single_city point resting on an institution is a single_city
        # point: the first stratum it fits.
        (
            ("city_municipality", "2915670", "place", "single_city", "institution"),
            P_PUBCITY,
        ),
        (
            ("city_municipality", "2915670", "place", "focus_model", "institution"),
            P_INSTITUTION,
        ),
        (("elsewhere_to_local", "2915670", "place", "focus_model", "named"), P_MODEL),
        (("city_municipality", "", "", "", ""), P_NONE),
        (("statewide", "29", "state", "", ""), S_STATE),
        (("national", "", "", "", ""), N_NONE),
        (("other", "", "", "", ""), N_NONE),
        ((None, "", "", "", ""), None),
        (("local", "", "", "", ""), None),
    ],
)
def test_a_story_goes_to_the_first_stratum_it_fits(claim, stratum):
    assert stratum_of(*claim, 0) == stratum


@pytest.mark.parametrize("counties,stratum", [(0, R_NONE), (1, R_ONE), (5, R_MANY)])
def test_a_regional_story_is_stratified_by_its_counties(counties, stratum):
    assert stratum_of("regional", "", "", "", "", counties) == stratum


def test_the_regional_draw_is_split_in_proportion_and_sums_to_its_total():
    populations = {R_NONE: [1] * 313, R_ONE: [1] * 1727, R_MANY: [1] * 1935}
    wanted = _wanted({"regional": 185, S_STATE: 40}, populations)
    assert wanted[R_NONE] + wanted[R_ONE] + wanted[R_MANY] == 185
    assert wanted[R_ONE] == 80 and wanted[S_STATE] == 40


# --------------------------------------------------------------- the draw


def _story(n, scope, *, point=None, level=None, method=None, support=None, rows=()):
    from explorer.models import (
        Article,
        ArticleEnrichment,
        ArticleGeoid,
        CandidateLink,
        Source,
    )

    source, _ = Source.objects.using("crawler").get_or_create(
        id="s1", defaults={"host": "a.example", "canonical_name": "A Paper"}
    )
    link = CandidateLink.objects.using("crawler").create(
        id=f"cl-{n}", url=f"https://a.example/{n}", source=source, status="extracted"
    )
    article = Article.objects.using("crawler").create(
        id=f"a-{n}",
        candidate_link=link,
        url=link.url,
        title=f"Story {n}",
        status="enriched",
        text="word " * 150,
        dataset_id="d1",
    )
    ArticleEnrichment.objects.using("crawler").create(
        article=article,
        profile_version=3,
        cost_usd="0",
        scope=scope,
        scope_confidence=0.9,
        point_place="Columbia" if point else None,
        point_geoid=point,
        point_geoid_level=level,
        point_method=method,
        point_support=support,
        point_lat=38.95 if point else None,
        point_lon=-92.33 if point else None,
    )
    for geoid, geoid_level, source_name in rows:
        ArticleGeoid.objects.using("crawler").create(
            article_id=article.id,
            geoid=geoid,
            geoid_level=geoid_level,
            is_primary=False,
            source=source_name,
        )
    return article


@pytest.fixture
def corpus(crawler_schema):
    """Twelve model-placed points, four assumed ones, six regional stories
    (two with no county, two with one, two with two), three statewide,
    two national."""
    n = iter(range(1000))
    for _ in range(12):
        _story(
            next(n),
            "city_municipality",
            point="2915670",
            level="place",
            method="focus_model",
            support="named",
            rows=[("2915670", "place", "point"), ("29019", "county", "county_rollup")],
        )
    for _ in range(4):
        _story(
            next(n),
            "city_municipality",
            point="2915670",
            level="place",
            method="publication_place_assumed",
        )
    for counties in (0, 0, 1, 1, 2, 2):
        rows = [(f"290{c:02d}", "county", "mention") for c in range(1, counties + 1)]
        _story(next(n), "regional", rows=rows + [("2932248", "place", "mention")])
    for _ in range(3):
        _story(next(n), "statewide", point="29", level="state", method="scope_state")
    for _ in range(2):
        _story(next(n), "national")


PLAN = {P_MODEL: 5, P_ASSUMED: 10, "regional": 3, S_STATE: 1, N_NONE: 2}


def _pilot(name="Geo pilot"):
    return open_round(name, Round.COMPARISON, task=GEOGRAPHY, base_coders=2)


def test_the_pilot_draws_each_stratum_and_weights_it_back(corpus):
    pilot = _pilot()
    cohort, report = draw_geography_pilot(pilot, plan=PLAN, seed=1)
    by = {row["stratum"]: row for row in report}
    assert by[P_MODEL]["drawn"] == 5 and by[P_MODEL]["population"] == 12
    # Asked for more than exist: all of them, reported short.
    assert by[P_ASSUMED]["drawn"] == 4 and by[P_ASSUMED]["wanted"] == 10
    assert by[N_NONE]["drawn"] == 2
    # Strata with nothing asked of them draw nothing.
    assert by[P_COUNTY]["drawn"] == 0 and by[P_INSTITUTION]["drawn"] == 0

    samples = list(cohort.samples.all())
    assert len(samples) == sum(row["drawn"] for row in report)
    # Each stratum's samples, weighted, are its population again.
    for stratum, row in by.items():
        weights = [1 / s.inclusion_probability for s in samples if s.stratum == stratum]
        if weights:
            assert sum(weights) == pytest.approx(row["population"])
    assert all(s.dataset_id == "d1" for s in samples)


def test_the_round_records_how_its_stories_were_drawn(corpus):
    pilot = _pilot()
    draw_geography_pilot(pilot, plan=PLAN, seed=7)
    pilot.refresh_from_db()
    assert pilot.draw_rule["seed"] == 7 and pilot.draw_rule["profile_version"] == 3
    assert pilot.draw_rule["plan"] == PLAN
    assert {row["stratum"] for row in pilot.draw_rule["strata"]} >= {P_MODEL, R_ONE}


def test_the_same_seed_draws_the_same_stories(corpus):
    first, _ = draw_geography_pilot(_pilot("A"), plan=PLAN, seed=3)
    drawn = set(first.samples.values_list("article_id", flat=True))
    ClassificationSample.objects.filter(cohort=first).delete()
    second, _ = draw_geography_pilot(_pilot("B"), plan=PLAN, seed=3)
    assert set(second.samples.values_list("article_id", flat=True)) == drawn


def test_every_drawn_story_has_its_claim_saved(corpus):
    pilot = _pilot()
    cohort, _ = draw_geography_pilot(pilot, plan=PLAN, seed=1)
    drawn = set(cohort.samples.values_list("article_id", flat=True))
    claims = {c.article_id: c for c in GeographyClaim.objects.filter(round=pilot)}
    assert set(claims) == drawn

    regional = [c for c in claims.values() if c.pipeline_class == "R"]
    assert regional and all(c.scope == "regional" for c in regional)
    assert all(not c.point_geoid for c in regional)
    two = [c for c in regional if len(c.counties) == 2]
    for claim in two:
        assert claim.counties == ["29001", "29002"]
        # The place mention is kept too, with its source.
        assert {"geoid": "2932248", "level": "place"}.items() <= {
            k: v for k, v in claim.geoid_rows[-1].items() if k in ("geoid", "level")
        }.items()

    state = next(c for c in claims.values() if c.pipeline_class == "S")
    assert state.state_geoid == "29"
    point = next(c for c in claims.values() if c.point_method == "focus_model")
    assert point.pipeline_class == "P" and point.point_geoid == "2915670"
    assert point.point_lat == 38.95 and point.scope_confidence == 0.9
    assert point.counties == ["29019"]


def test_a_claim_outlives_the_next_enrichment_run(corpus):
    """The crawler deletes and rewrites a story's geography each run."""
    from explorer.models import ArticleEnrichment, ArticleGeoid

    pilot = _pilot()
    draw_geography_pilot(pilot, plan=PLAN, seed=1)
    claim = GeographyClaim.objects.filter(round=pilot, point_method="focus_model")[0]
    before = (claim.point_geoid, claim.scope, list(claim.geoid_rows))

    ArticleGeoid.objects.using("crawler").filter(article_id=claim.article_id).delete()
    ArticleEnrichment.objects.using("crawler").filter(
        article_id=claim.article_id
    ).update(scope="regional", point_geoid=None, profile_version=4)

    claim = GeographyClaim.objects.get(pk=claim.pk)
    assert (claim.point_geoid, claim.scope, claim.geoid_rows) == before


def test_a_claim_is_never_edited(corpus):
    pilot = _pilot()
    draw_geography_pilot(pilot, plan=PLAN, seed=1)
    claim = GeographyClaim.objects.filter(round=pilot).first()
    claim.point_geoid = "29095"
    with pytest.raises(ValueError, match="never edited"):
        claim.save()


def test_a_story_in_one_geography_pilot_is_not_drawn_into_another(corpus):
    first, _ = draw_geography_pilot(_pilot("A"), plan=PLAN, seed=1)
    taken = set(first.samples.values_list("article_id", flat=True))
    second, _ = draw_geography_pilot(_pilot("B"), plan=PLAN, seed=1)
    assert not taken & set(second.samples.values_list("article_id", flat=True))


def test_a_story_in_a_cin_batch_is_still_drawn_into_a_geography_pilot(corpus):
    cin = ClassificationCohort.objects.create(number=900, slug="cohort-900")
    for n in range(30):
        ClassificationSample.objects.create(
            cohort=cin, article_id=f"a-{n}", stratum="random"
        )
    cohort, report = draw_geography_pilot(_pilot(), plan=PLAN, seed=1)
    assert sum(row["drawn"] for row in report) == cohort.samples.count() > 0


def test_opening_a_geography_pilot_draws_assigns_and_says_what_it_did(corpus):
    pilot = _pilot()
    coders = [User.objects.create_user(n, email=f"{n}@x.org") for n in "abcd"]
    for user in coders:
        set_standing(user, pilot.codebook_version, CoderStanding.ADMITTED)
    cohort, level, message = open_batch(pilot)
    assert cohort is not None
    for user in coders:
        Grant.objects.create(
            user=user, app=DATADESK, scope=cohort.slug, role=CLASSIFIER
        )
    assign_cohort(cohort)
    # Two coders on every story.
    per_story = {}
    for article_id in ClassificationAssignment.objects.filter(
        cohort=cohort
    ).values_list("article_id", flat=True):
        per_story[article_id] = per_story.get(article_id, 0) + 1
    assert set(per_story.values()) == {2}
    assert "placement of each is saved" in message
    assert CodebookVersion.current().task == "cin"
