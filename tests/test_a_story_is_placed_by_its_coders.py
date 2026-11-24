"""A coder says where a story is about, on a map.

The geography question rides on the CIN rounds: a round, its batches,
assignments and coder standing are the same tables, and only the
question and the answer are new (docs/GEOGRAPHY_ICR_DATADESK.md, G-WP1
to G-WP3). These tests hold the joins between the two: a geography round
never borrows the CIN codebook, never takes a CIN story off the table,
and a coder answering both questions of one story is asked both.
"""

import json

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
    Round,
    open_round,
)
from review.geo_resolve import resolve
from review.geography_icr import (
    GEOGRAPHY,
    NO_PLACE,
    PIN_VIEW_KM,
    PLACE,
    REGION,
    STATE,
    UNSTATED,
    GeographyDecision,
    clean_answer,
    geography_guide,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/review/classification/geography/"
CIN_URL = "/review/classification/"

COLUMBIA = {"lat": 38.9517, "lon": -92.3341, "view_km": 20}
ASHLAND = {"lat": 38.7745, "lon": -92.2571, "view_km": 20}
JEFFERSON_CITY = {"lat": 38.5767, "lon": -92.1735, "view_km": 20}


# ------------------------------------------------------------ the answer


def test_one_place_is_one_pin_resolved_to_its_place_and_county():
    answer = clean_answer(PLACE, json.dumps([COLUMBIA]))
    assert answer["counties"] == ["29019"] and answer["state"] == "29"
    (pin,) = answer["pins"]
    assert pin["place"] == "2915670" and pin["place_name"] == "Columbia"


def test_the_servers_resolution_is_kept_not_the_browsers():
    """Whatever the form says a pin resolved to is thrown away."""
    forged = dict(COLUMBIA, place="2938000", county="29095")
    (pin,) = clean_answer(PLACE, [forged])["pins"]
    assert pin["place"] == "2915670" and pin["county"] == "29019"


@pytest.mark.parametrize(
    "pins,message",
    [
        ([], "exactly one"),
        ([COLUMBIA, ASHLAND], "exactly one"),
        ([dict(COLUMBIA, view_km=PIN_VIEW_KM + 1)], "Zoom in"),
        ([{"lat": 38.95, "lon": -92.33}], "Zoom in"),
        ([{"lat": 28.0, "lon": -90.0, "view_km": 20}], "not in a U.S. county"),
        ([{"lat": "x", "lon": -92.3, "view_km": 20}], "without a position"),
    ],
)
def test_a_place_answer_that_is_not_one_good_pin_is_refused(pins, message):
    with pytest.raises(ValueError, match=message):
        clean_answer(PLACE, pins)


def test_a_town_typed_or_an_address_needs_no_zoom_and_says_how_it_came():
    """The zoom rule is for a click. A town chosen by name is pinned inside
    its boundary, an address at the geocoded point: neither has a zoom."""
    typed = {"lat": 38.95, "lon": -92.33, "how": "search"}
    (pin,) = clean_answer(PLACE, [typed])["pins"]
    assert (pin["how"], pin["view_km"], pin["place"]) == ("search", None, "2915670")
    (pin,) = clean_answer(PLACE, [dict(typed, how="address")])["pins"]
    assert pin["how"] == "address"
    (pin,) = clean_answer(PLACE, [COLUMBIA])["pins"]
    assert (pin["how"], pin["view_km"]) == ("map", 20)
    with pytest.raises(ValueError, match="form the map does not make"):
        clean_answer(PLACE, [dict(typed, how="guess")])


def test_a_region_is_its_counties_and_the_counties_of_its_pins():
    answer = clean_answer(REGION, [JEFFERSON_CITY], ["29019", "29027"])
    assert answer["counties"] == ["29019", "29027", "29051"]
    assert answer["state"] == "29"


def test_a_region_across_a_state_line_has_no_one_state():
    answer = clean_answer(REGION, [], ["29095", "20209"])
    assert answer["state"] == ""


@pytest.mark.parametrize(
    "pins,counties,message",
    [
        ([], [], "Add the counties or the towns"),
        ([], ["99999"], "not one the map knows"),
        ([], [f"29{n:03d}" for n in range(1, 100, 2)], "More than 40"),
    ],
)
def test_a_region_answer_needs_real_counties(pins, counties, message):
    with pytest.raises(ValueError, match=message):
        clean_answer(REGION, pins, counties)


def test_the_whole_state_is_a_state():
    assert clean_answer(STATE, state="29")["state"] == "29"
    with pytest.raises(ValueError, match="Choose the state"):
        clean_answer(STATE, state="99")


@pytest.mark.parametrize("answer_class", [UNSTATED, NO_PLACE])
def test_unstated_and_no_place_carry_no_geography(answer_class):
    """Even if the map was used before the class was changed."""
    answer = clean_answer(answer_class, [COLUMBIA], ["29019"], "29")
    assert answer == {
        "answer_class": answer_class,
        "pins": [],
        "counties": [],
        "state": "",
    }


def test_an_answer_needs_a_class():
    with pytest.raises(ValueError, match="what kind of place"):
        clean_answer("", [COLUMBIA])


def test_garbled_map_answer_is_refused_in_words():
    with pytest.raises(ValueError, match="could not be read"):
        clean_answer(PLACE, "{not json")


# ------------------------------------------------------------- the round


def test_a_geography_round_is_coded_under_the_geography_guide():
    created = open_round("Geo practice", Round.PRACTICE, task=GEOGRAPHY)
    assert created.is_geography
    assert created.codebook_version.task == GEOGRAPHY
    assert created.codebook_version.name == "GEO-1"


def test_the_geography_guide_is_never_the_current_cin_codebook():
    """It is the newest version frozen, and a CIN round opened with no
    codebook named would otherwise be coded under it."""
    cin = CodebookVersion.v1()
    geography_guide()
    assert CodebookVersion.current() == cin
    assert open_round("CIN", Round.PRACTICE).codebook_version == cin


def _article(n, *, status="enriched", profile=3, dataset=None):
    from explorer.models import Article, ArticleEnrichment, CandidateLink, Source

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
        status=status,
        text="word " * 150,
        primary_label="Civic Life",
        primary_label_confidence=0.9,
        dataset_id=dataset,
    )
    ArticleEnrichment.objects.using("crawler").create(
        article=article,
        profile_version=profile,
        cost_usd="0",
        scope="city_municipality",
    )
    return article


def test_a_geography_practice_draws_enriched_stories_under_the_current_profile(
    crawler_schema,
):
    from review.classification import open_batch

    for n in range(6):
        _article(n)
    _article(10, status="labeled")
    _article(11, profile=2)
    created = open_round("Geo practice", Round.PRACTICE, task=GEOGRAPHY)
    cohort, level, message = open_batch(created, 20)
    drawn = set(cohort.samples.values_list("article_id", flat=True))
    assert drawn == {f"a-{n}" for n in range(6)}
    assert level == "warn" and "6 of 20 enriched stories drawn" in message


def test_a_story_in_a_cin_batch_can_still_be_drawn_for_geography(crawler_schema):
    """The two questions measure different things, so a story one has
    drawn is not off the table for the other."""
    from review.classification import open_batch

    _article(1)
    cin = ClassificationCohort.objects.create(number=900, slug="cohort-900")
    ClassificationSample.objects.create(cohort=cin, article_id="a-1", stratum="random")
    created = open_round("Geo practice", Round.PRACTICE, task=GEOGRAPHY)
    cohort, _, _ = open_batch(created, 5)
    assert list(cohort.samples.values_list("article_id", flat=True)) == ["a-1"]


def test_a_geography_pilot_with_nothing_to_draw_says_so(crawler_schema):
    from review.classification import open_batch

    created = open_round("Geo pilot", Round.COMPARISON, task=GEOGRAPHY)
    cohort, level, message = open_batch(created, 185)
    assert cohort is None and level == "bad" and "No enriched stories" in message


# ------------------------------------------------------- the admin page


@pytest.fixture
def admin(db):
    user = User.objects.create_user("boss", password="x")
    Grant.objects.create(user=user, app=DATADESK, role=ADMIN, scope="")
    return user


def test_an_admin_starts_a_geography_practice(client, admin, crawler_schema):
    for n in range(3):
        _article(n)
    CodebookVersion.v1()
    client.force_login(admin)
    response = client.post(
        reverse("review:cin_rounds"),
        {"name": "Geo practice", "kind": Round.PRACTICE, "task": GEOGRAPHY},
        follow=True,
    )
    created = Round.objects.get(name="Geo practice")
    assert created.is_geography and created.codebook_version.name == "GEO-1"
    assert created.cohorts.get().samples.count() == 3
    assert response.status_code == 200
    listing = client.get(reverse("review:cin_rounds")).content.decode()
    assert "geography" in listing
    # The geography guide is not offered as a CIN codebook.
    assert "GEO-1</option>" not in listing


def test_a_geography_training_round_is_refused(client, admin):
    client.force_login(admin)
    response = client.post(
        reverse("review:cin_rounds"),
        {"name": "Geo training", "kind": Round.COLLECTION, "task": GEOGRAPHY},
    )
    assert "a practice or a pilot" in response.content.decode()
    assert not Round.objects.filter(name="Geo training").exists()


# ------------------------------------------------------- the coder's screen


@pytest.fixture
def geo_round(db):
    return open_round("Geo practice", Round.PRACTICE, task=GEOGRAPHY)


@pytest.fixture
def coder(db):
    return User.objects.create_user("cl", email="cl@localnewsimpact.org")


@pytest.fixture
def assigned(crawler_schema, geo_round, coder):
    _article(1)
    cohort = ClassificationCohort.objects.create(
        number=1,
        slug="cohort-1",
        round=geo_round,
        codebook_version=geo_round.codebook_version,
    )
    ClassificationSample.objects.create(
        cohort=cohort, article_id="a-1", stratum="random", inclusion_probability=0.5
    )
    Grant.objects.create(user=coder, app=DATADESK, scope=cohort.slug, role=CLASSIFIER)
    return ClassificationAssignment.objects.create(
        cohort=cohort, article_id="a-1", assigned_to=coder
    )


def test_a_geography_assignment_is_served_with_the_map(client, coder, assigned):
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert "Where is this story about?" in body
    assert 'id="geo-map"' in body and "geography-coding.js" in body
    assert "Story 1" in body
    # Not the CIN menu, and nothing that names the outlet or the
    # pipeline's placement.
    assert "Primary category" not in body
    assert "a.example" not in body and "A Paper" not in body
    assert "city_municipality" not in body


def test_a_saved_answer_is_resolved_by_the_server(client, coder, assigned):
    client.force_login(coder)
    client.post(
        URL,
        {
            "assignment": assigned.pk,
            "answer_class": PLACE,
            "pins": json.dumps([COLUMBIA]),
            "counties": "[]",
            "opened_at": "41",
        },
    )
    decision = GeographyDecision.objects.get()
    assert decision.answer_class == PLACE and decision.place == "2915670"
    assert decision.counties == ["29019"] and decision.state == "29"
    assert decision.codebook_version.name == "GEO-1"
    assert decision.inclusion_probability == 0.5 and decision.seconds_spent == 41
    assigned.refresh_from_db()
    assert assigned.completed_at is not None


def test_a_refused_answer_leaves_the_story_in_the_queue(client, coder, assigned):
    client.force_login(coder)
    response = client.post(
        URL,
        {
            "assignment": assigned.pk,
            "answer_class": PLACE,
            "pins": json.dumps([dict(COLUMBIA, view_km=400)]),
        },
        follow=True,
    )
    assert "Zoom in until the town fills the map" in response.content.decode()
    assert not GeographyDecision.objects.exists()
    assigned.refresh_from_db()
    assert assigned.completed_at is None


def test_a_cin_answer_on_the_story_does_not_hide_the_geography_question(
    client, coder, assigned
):
    other = ClassificationCohort.objects.create(number=2, slug="cohort-2")
    ClassificationDecision.objects.create(
        cohort=other, article_id="a-1", decided_by=coder, primary_label="Sports"
    )
    client.force_login(coder)
    assert "Where is this story about?" in client.get(URL).content.decode()


def test_a_geography_answer_does_not_hide_the_cin_question(client, coder, assigned):
    other = ClassificationCohort.objects.create(number=2, slug="cohort-2")
    Grant.objects.create(user=coder, app=DATADESK, scope=other.slug, role=CLASSIFIER)
    ClassificationAssignment.objects.create(
        cohort=other, article_id="a-1", assigned_to=coder
    )
    GeographyDecision.objects.create(
        cohort=assigned.cohort, article_id="a-1", decided_by=coder, answer_class="N"
    )
    assigned.completed_at = "2026-11-23T00:00:00Z"
    assigned.save()
    client.force_login(coder)
    assert "Primary category" in client.get(CIN_URL).content.decode()


def test_a_cin_form_cannot_answer_a_geography_assignment(client, coder, assigned):
    client.force_login(coder)
    client.post(URL, {"assignment": assigned.pk, "answer_class": "Z"})
    client.post(URL, {"assignment": assigned.pk, "primary": "Sports"})
    assert not GeographyDecision.objects.exists()


def test_the_pin_lookup_answers_a_coder(client, coder, assigned):
    client.force_login(coder)
    where = client.get(
        reverse("review:geography_coding_resolve"), {"lat": 38.9517, "lon": -92.3341}
    ).json()
    assert where["place_name"] == "Columbia" and where["county"] == "29019"
    assert (
        client.get(reverse("review:geography_coding_resolve"), {"lat": "x"}).status_code
        == 400
    )


def test_the_place_search_moves_the_map_for_a_coder(client, coder, assigned):
    client.force_login(coder)
    url = reverse("review:geography_coding_find")
    hits = client.get(url, {"q": "Ashland", "state": "MO"}).json()["results"]
    assert hits[0]["geoid"] == "2902242"
    # A town in a built state carries its box, for the map to fit to.
    west, south, east, north = hits[0]["bbox"]
    assert west < -92.25 < east and south < 38.78 < north
    assert client.get(url, {"q": "A"}).json() == {"results": []}


def test_a_state_after_a_comma_ranks_the_search(client, coder, assigned):
    """On a Washington story eight other Fultons came before Missouri's."""
    client.force_login(coder)
    url = reverse("review:geography_coding_find")
    for typed in ("Fulton, MO", "Fulton, Missouri"):
        first = client.get(url, {"q": typed, "state": "53"}).json()["results"][0]
        found = (first["name"], first["state"], first["kind"])
        assert found == ("Fulton", "MO", "place")
        # A town carries a point inside it, for a pin chosen by name.
        assert resolve(*first["point"]).place == first["geoid"]
    # Exact names first: "Ashland" on a Washington story is an Ashland,
    # not Island County.
    hits = client.get(url, {"q": "Ashland", "state": "53"}).json()["results"]
    assert hits[0]["name"] == "Ashland"


def test_a_search_naming_a_county_offers_counties(client, coder, assigned):
    """Towns named Boone filled every slot before the county rung."""
    client.force_login(coder)
    url = reverse("review:geography_coding_find")
    hits = client.get(url, {"q": "Boone County, MO", "state": "29"}).json()["results"]
    assert {h["kind"] for h in hits} == {"county"}
    assert (hits[0]["geoid"], hits[0]["name"]) == ("29019", "Boone")
    assert hits[0]["bbox"] is not None


def test_the_map_is_leaflet_over_tiles_keyed_only_where_a_key_is_set(
    client, coder, assigned, settings
):
    client.force_login(coder)
    settings.CARTO_BASEMAPS_KEY = ""
    body = client.get(URL).content.decode()
    assert "leaflet/leaflet.js" in body and 'data-tile-key=""' in body
    assert reverse("review:geography_coding_address") in body
    settings.CARTO_BASEMAPS_KEY = "k-123"
    assert 'data-tile-key="k-123"' in client.get(URL).content.decode()


def test_an_address_is_looked_up_census_first_then_openstreetmap(
    client, coder, assigned, monkeypatch
):
    from django.core.cache import cache

    from review import geocode

    asked = []

    def census_hit(url, params):
        asked.append(url)
        if url == geocode.CENSUS:
            return {
                "result": {
                    "addressMatches": [
                        {
                            "matchedAddress": "N PROVIDENCE RD & E BROADWAY, COLUMBIA",
                            "coordinates": {"x": -92.334, "y": 38.952},
                        }
                    ]
                }
            }
        raise AssertionError("OpenStreetMap asked after a Census match")

    cache.clear()
    monkeypatch.setattr(geocode, "_get", census_hit)
    client.force_login(coder)
    url = reverse("review:geography_coding_address")
    (hit,) = client.get(url, {"q": "Providence and Broadway, Columbia MO"}).json()[
        "results"
    ]
    assert (hit["lat"], hit["lon"], hit["source"]) == (38.952, -92.334, "Census")
    assert client.get(url, {"q": "ab"}).json() == {"results": []}

    def landmark(url, params):
        asked.append(url)
        if url == geocode.CENSUS:
            return {"result": {"addressMatches": []}}
        return [
            {
                "display_name": "Arrowhead Stadium, Kansas City",
                "lat": "39.0489",
                "lon": "-94.4840",
                "boundingbox": ["39.047", "39.050", "-94.486", "-94.482"],
            }
        ]

    asked.clear()
    monkeypatch.setattr(geocode, "_get", landmark)
    (hit,) = client.get(url, {"q": "Arrowhead Stadium"}).json()["results"]
    assert asked == [geocode.CENSUS, geocode.NOMINATIM]
    assert hit["source"] == "OpenStreetMap"
    assert hit["bbox"] == [-94.486, 39.047, -94.482, 39.05]


def test_a_geocoder_that_fails_is_asked_again_next_time(monkeypatch):
    from django.core.cache import cache

    from review import geocode

    cache.clear()

    def down(url, params):
        raise OSError("timed out")

    monkeypatch.setattr(geocode, "_get", down)
    assert geocode.look_up("100 E Broadway, Columbia, MO") == []

    def up(url, params):
        return {
            "result": {
                "addressMatches": [
                    {
                        "matchedAddress": "100 E BROADWAY",
                        "coordinates": {"x": -92.3, "y": 38.9},
                    }
                ]
            }
        }

    monkeypatch.setattr(geocode, "_get", up)
    assert geocode.look_up("100 E Broadway, Columbia, MO")[0]["source"] == "Census"


def test_a_geography_coder_with_nothing_left_is_not_told_about_categories(
    client, coder, assigned
):
    GeographyDecision.objects.create(
        cohort=assigned.cohort, article_id="a-1", decided_by=coder, answer_class="N"
    )
    client.force_login(coder)
    body = client.get(URL).content.decode()
    assert "Where is this story about?" in body
    assert "information need" not in body


# ------------------------------------------------------- one queue each


def test_each_question_has_its_own_queue(client, coder, assigned):
    """Labeling -> CIN categories never serves a geography story, and
    Labeling -> Story geography never serves a CIN one."""
    other = ClassificationCohort.objects.create(number=2, slug="cohort-2")
    Grant.objects.create(user=coder, app=DATADESK, scope=other.slug, role=CLASSIFIER)
    ClassificationAssignment.objects.create(
        cohort=other, article_id="a-1", assigned_to=coder
    )
    client.force_login(coder)
    cin = client.get(CIN_URL).content.decode()
    assert "Primary category" in cin and 'id="geo-map"' not in cin
    where = client.get(URL).content.decode()
    assert 'id="geo-map"' in where and "Primary category" not in where


def test_an_empty_cin_queue_is_not_filled_by_geography_work(client, coder, assigned):
    client.force_login(coder)
    body = client.get(CIN_URL).content.decode()
    assert "Nothing assigned to you" in body and 'id="geo-map"' not in body


def test_both_queues_sit_under_one_labeling_header(client, coder, assigned):
    client.force_login(coder)
    body = client.get(URL).content.decode()
    nav = body[body.index('id="site-nav"') :]
    nav = nav[: nav.index("</nav>")]
    assert "Labeling" in nav
    assert nav.index("Labeling") < nav.index("CIN categories")
    assert nav.index("CIN categories") < nav.index("Story geography")
    assert ">Classification<" not in nav


# ------------------------------------------------- outside the story's state


def test_an_answer_outside_the_storys_state_is_refused():
    """A story about somewhere in another state is answered N."""
    from review.geography_icr import STATE

    with pytest.raises(ValueError, match="Outside the state"):
        clean_answer(STATE, state="20", home_state="29")
    assert clean_answer(STATE, state="29", home_state="29")["state"] == "29"
    with pytest.raises(ValueError, match="Outside the state"):
        clean_answer(PLACE, [COLUMBIA], home_state="20")
    assert clean_answer(PLACE, [COLUMBIA], home_state="29")["counties"]


def test_a_regional_answer_reaching_into_the_state_is_kept():
    from review.geography_icr import REGION, outside_home

    answer = {"answer_class": REGION, "counties": ["20091", "29095"]}
    assert not outside_home(answer, "29")
    answer = {"answer_class": REGION, "counties": ["20091", "20209"]}
    assert outside_home(answer, "29")


def test_a_claim_outside_the_storys_state_is_scored_as_outside():
    from review.geography_icr import (
        NO_PLACE,
        REGION,
        STATE,
        GeographyClaim,
        score_outside_as_none,
    )

    kansas = GeographyClaim(
        pipeline_class=STATE, point_geoid="20", point_geoid_level="state"
    )
    assert score_outside_as_none(kansas, "29").pipeline_class == NO_PLACE
    home = GeographyClaim(
        pipeline_class=STATE, point_geoid="29", point_geoid_level="state"
    )
    assert score_outside_as_none(home, "29").pipeline_class == STATE
    border = GeographyClaim(
        pipeline_class=REGION,
        geoid_rows=[
            {"geoid": "20091", "level": "county"},
            {"geoid": "29095", "level": "county"},
        ],
    )
    assert score_outside_as_none(border, "29").pipeline_class == REGION


def test_quincy_illinois_in_a_missouri_dataset_is_not_a_disagreement():
    """The pipeline placed the story in Quincy, Ill.; the coder answered
    "Outside the state". That is agreement. Hannibal, Mo. is not."""
    from review.geography_icr import (
        NO_PLACE,
        GeographyClaim,
        _county_value,
        pipeline_value,
        score_outside_as_none,
    )

    quincy = GeographyClaim(
        pipeline_class=PLACE,
        point_geoid="1762367",
        point_geoid_level="place",
        point_lat=39.9356,
        point_lon=-91.4099,
    )
    scored = score_outside_as_none(quincy, "29")
    assert scored.pipeline_class == NO_PLACE
    assert pipeline_value(scored) == _county_value(NO_PLACE, [], "")
    # No coordinates: the GEOID's state digits decide.
    bare = GeographyClaim(
        pipeline_class=PLACE, point_geoid="1762367", point_geoid_level="place"
    )
    assert score_outside_as_none(bare, "29").pipeline_class == NO_PLACE
    hannibal = GeographyClaim(
        pipeline_class=PLACE,
        point_geoid="2930214",
        point_geoid_level="place",
        point_lat=39.7084,
        point_lon=-91.3585,
    )
    assert score_outside_as_none(hannibal, "29").pipeline_class == PLACE
