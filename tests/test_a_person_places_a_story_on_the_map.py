"""Geography a person put in has to reach the map, or the queue is a
form that records opinions nothing reads.

The first cut of this feature wrote the contribution to
`article_places_manual` and merged it into `article_geoids` -- and a
story map reads NEITHER. It draws dots from `enrichment.point_lat/lon`
and shades from `enrichment.geoids`, so a reviewer's work changed
nothing on screen and nothing said why.

These tests run `run_story_map` against a contribution and assert the
payload, which is the only claim worth making: not that a row was
written, but that the map drew it.
"""

import datetime as dt

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def dataset(crawler_schema):
    from explorer.models import Dataset

    return Dataset.objects.create(
        id="d1", slug="mizzou", label="Missouri", meta={"default_state": "MO"}
    )


@pytest.fixture
def newsroom(dataset):
    from explorer.models import DatasetSource, Source

    source = Source.objects.create(
        id="s1",
        host="unterrified.example",
        host_norm="unterrified.example",
        canonical_name="The Unterrified Democrat",
        city="Linn",
        county="Osage",
        owner="Independent",
        meta={"state": "MO"},
    )
    DatasetSource.objects.create(id="ds1", dataset=dataset, source=source)
    return source


@pytest.fixture
def unplaced(dataset, newsroom):
    """Two stories enrichment could not place, as the queue finds them.

    `point_lat` NULL and `geoids` empty is exactly what
    `needs_geography` selects on, so this is the state a reviewer meets.
    """
    from explorer.models import Article, ArticleEnrichment, CandidateLink

    link = CandidateLink.objects.create(
        id="cl-geo", url="https://unterrified.example/1", source=newsroom
    )
    made = []
    for i in range(2):
        article = Article.objects.create(
            id=f"unplaced{i}",
            status="enrichment_skipped",
            candidate_link=link,
            publish_date=timezone.make_aware(dt.datetime(2026, 3, 10 + i, 12)),
        )
        ArticleEnrichment.objects.create(
            article=article,
            scope="local",
            skip_reason="paywall_stub",
            cost_usd="0.00",
        )
        made.append(article)
    return made


def _map(dataset):
    from visuals.corpus import run_story_map

    return run_story_map(
        {"kind": "story_map", "datasets": [dataset.slug]}, [dataset.slug]
    )


def test_without_a_contribution_the_map_is_empty(dataset, unplaced):
    """The baseline the assertions below are measured against."""
    payload = _map(dataset)
    assert payload["points"] == []
    assert payload["areas"] == []


def test_a_human_centre_becomes_a_dot(dataset, unplaced):
    """A reviewer types a name and never a position, so the coordinates
    come from the Census internal point for the geoid -- a point
    guaranteed to lie inside the shape."""
    from explorer.models import ArticlePlaceManual

    # Linn, Missouri.
    ArticlePlaceManual.objects.create(
        article=unplaced[0],
        full_name="Linn",
        city="Linn",
        state="MO",
        geoid="2943238",
        geoid_level="place",
        is_point=True,
        added_by="someone@example.org",
    )

    payload = _map(dataset)
    assert len(payload["points"]) == 1
    dot = payload["points"][0]
    assert dot["geoid"] == "2943238"
    assert dot["place"] == "Linn"
    assert dot["stories"] == 1
    # Drawn, not merely recorded: without a coordinate there is no dot.
    assert dot["lat"] is not None and dot["lon"] is not None
    assert 38 < dot["lat"] < 39, dot["lat"]
    assert -92.5 < dot["lon"] < -91.5, dot["lon"]


def test_a_human_centre_also_shades_its_county(dataset, unplaced):
    """The same as the pipeline, whose point rolls up to its county too."""
    from explorer.models import ArticlePlaceManual

    ArticlePlaceManual.objects.create(
        article=unplaced[0],
        full_name="Linn",
        city="Linn",
        state="MO",
        geoid="2943238",
        geoid_level="place",
        is_point=True,
        added_by="someone@example.org",
    )
    payload = _map(dataset)
    # Linn is in Osage County.
    assert [a["geoid"] for a in payload["areas"]] == ["29151"]
    assert payload["areas"][0]["county name"]
    assert payload["areas"][0]["stories"] == 1


def test_several_mentions_shade_several_counties(dataset, unplaced):
    """A story mentions several places and the pipeline records several,
    so a person saying the same thing must shade the same way."""
    from explorer.models import ArticlePlaceManual

    for name, geoid in (("Linn", "2943238"), ("Columbia", "2915670")):
        ArticlePlaceManual.objects.create(
            article=unplaced[0],
            full_name=name,
            city=name,
            state="MO",
            geoid=geoid,
            geoid_level="place",
            is_point=False,
            added_by="someone@example.org",
        )

    payload = _map(dataset)
    shaded = {a["geoid"] for a in payload["areas"]}
    # Osage and Boone.
    assert shaded == {"29151", "29019"}, shaded
    # And no dot: mentions are not a centre.
    assert payload["points"] == []


def test_a_story_with_no_centre_still_reaches_the_map(dataset, unplaced):
    """A game between two towns has two mentions and no central
    location. A map that only draws centres loses it entirely."""
    from explorer.models import ArticlePlaceManual

    for name, geoid in (("Linn", "2943238"), ("Westphalia", "2978910")):
        ArticlePlaceManual.objects.create(
            article=unplaced[0],
            full_name=name,
            city=name,
            state="MO",
            geoid=geoid,
            geoid_level="place",
            is_point=False,
            added_by="someone@example.org",
        )
    payload = _map(dataset)
    assert payload["points"] == []
    # Both towns are in Osage County, and the story counts once.
    assert [a["geoid"] for a in payload["areas"]] == ["29151"]
    assert payload["areas"][0]["stories"] == 1


def test_the_county_rung_shades_without_a_place(dataset, unplaced):
    """Frankenstein is a real village in Osage County and is in no
    gazetteer, so a reviewer enters the county. `to_county` takes the
    rung: passing "place" for a county code resolves nothing at all."""
    from explorer.models import ArticlePlaceManual

    ArticlePlaceManual.objects.create(
        article=unplaced[0],
        full_name="Osage County",
        county="Osage",
        state="MO",
        geoid="29151",
        geoid_level="county",
        is_point=False,
        added_by="someone@example.org",
    )
    payload = _map(dataset)
    assert [a["geoid"] for a in payload["areas"]] == ["29151"]


def test_a_human_centre_does_not_double_count_a_placed_story(dataset, unplaced):
    """Where both exist both are RECORDED -- neither is evidence the
    other is wrong -- but only one can be the dot for one story."""
    from explorer.models import ArticleEnrichment, ArticlePlaceManual

    enrichment = ArticleEnrichment.objects.get(article=unplaced[0])
    enrichment.point_geoid = "2915670"
    enrichment.point_geoid_level = "place"
    enrichment.point_place = "Columbia"
    enrichment.point_lat = 38.95
    enrichment.point_lon = -92.33
    enrichment.save()

    ArticlePlaceManual.objects.create(
        article=unplaced[0],
        full_name="Linn",
        city="Linn",
        state="MO",
        geoid="2943238",
        geoid_level="place",
        is_point=True,
        added_by="someone@example.org",
    )

    payload = _map(dataset)
    # One story, one dot -- the pipeline's, because it had one.
    assert len(payload["points"]) == 1
    assert payload["points"][0]["geoid"] == "2915670"
    # The human row is not discarded: it still shades Osage.
    assert "29151" in {a["geoid"] for a in payload["areas"]}


def test_a_human_centre_merges_into_the_dot_already_there(dataset, newsroom, unplaced):
    """Two dots for one place is not a second place.

    Appending gave Linn two dots at the same coordinates -- one of 21
    stories and one of 1 -- stacked so the second was invisible and the
    tooltip showed whichever drew last. Found by submitting a real
    decision and reading the payload, not by reading the code.
    """
    import datetime as dt

    from django.utils import timezone

    from explorer.models import (
        Article,
        ArticleEnrichment,
        ArticlePlaceManual,
        CandidateLink,
    )

    # One story the pipeline placed in Linn.
    link = CandidateLink.objects.get(id="cl-geo")
    placed = Article.objects.create(
        id="placed-in-linn",
        status="enriched",
        candidate_link=link,
        publish_date=timezone.make_aware(dt.datetime(2026, 3, 12, 12)),
    )
    ArticleEnrichment.objects.create(
        article=placed,
        scope="local",
        point_place="Linn",
        point_geoid="2943238",
        point_geoid_level="place",
        point_lat=38.478798,
        point_lon=-91.844989,
        cost_usd="0.01",
    )
    # And one a person placed in the same town.
    ArticlePlaceManual.objects.create(
        article=unplaced[0],
        full_name="Linn",
        city="Linn",
        state="MO",
        geoid="2943238",
        geoid_level="place",
        is_point=True,
        added_by="someone@example.org",
    )

    payload = _map(dataset)
    linn = [p for p in payload["points"] if p["geoid"] == "2943238"]
    assert len(linn) == 1, linn
    assert linn[0]["stories"] == 2
    # One newsroom, counted once -- a union, not a sum.
    assert linn[0]["publishers"] == 1


def test_the_cache_stamp_moves_when_somebody_places_a_story():
    """A reviewer creates no article, moves no membership and
    re-enriches nothing. Without a fourth part the stamp does not change,
    the keys do not move, and the map goes on drawing what it had before
    the queue was worked."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    body = (
        (root / "visuals/corpus.py")
        .read_text()
        .split("def corpus_version(")[1]
        .split("\ndef ")[0]
    )
    assert "ArticlePlaceManual" in body, "a contribution does not move the stamp"


# --- a person's contribution is enrichment ------------------------------------


@pytest.fixture
def cleaned_story(dataset, newsroom):
    """An article extraction finished with and enrichment could not place.

    `cleaned` is not in `ENRICHED_STATUSES`, so a visual asking for the
    enriched subset never saw it -- which is the state 7 of the first 12
    articles a reviewer placed were in.
    """
    from explorer.models import Article, ArticleEnrichment, CandidateLink

    link = CandidateLink.objects.get_or_create(
        id="cl-clean", defaults={"url": "https://u.example/c", "source": newsroom}
    )[0]
    article = Article.objects.create(
        id="cleaned1",
        status="cleaned",
        candidate_link=link,
        publish_date=timezone.make_aware(dt.datetime(2026, 3, 14, 12)),
    )
    ArticleEnrichment.objects.create(
        article=article, scope="local", skip_reason="paywall_stub", cost_usd="0.00"
    )
    return article


def _enriched_spec(dataset):
    return {
        "kind": "story_map",
        "shape": "story_map",
        "subset": "enriched",
        "datasets": [dataset.slug],
    }


def test_a_cleaned_story_is_not_in_the_enriched_subset_on_its_own(
    dataset, cleaned_story
):
    """The existing rule, asserted so the change below is visibly a
    widening and not a replacement."""
    from visuals.corpus import _base_queryset

    ids = set(
        _base_queryset(_enriched_spec(dataset), [dataset.slug]).values_list(
            "id", flat=True
        )
    )
    assert "cleaned1" not in ids


def test_a_story_a_person_placed_is_enriched_however_the_pipeline_left_it(
    dataset, cleaned_story
):
    """A story behind a paywall arrives as a headline and a subscription
    prompt. The pipeline cannot place it and stops; when a reviewer reads
    the link and says where it is, the article IS enriched -- not
    completely, but to the extent an article under that constraint can
    be.

    Without this the queue offers articles the corpus then refuses, and
    the work is done for nothing."""
    from explorer.models import ArticlePlaceManual
    from visuals.corpus import _base_queryset

    ArticlePlaceManual.objects.create(
        article=cleaned_story,
        full_name="Linn",
        city="Linn",
        state="MO",
        geoid="2943238",
        geoid_level="place",
        is_point=True,
        added_by="someone@example.org",
    )

    ids = set(
        _base_queryset(_enriched_spec(dataset), [dataset.slug]).values_list(
            "id", flat=True
        )
    )
    assert "cleaned1" in ids


def test_it_draws_on_the_map_and_not_merely_in_the_queryset(dataset, cleaned_story):
    """The queryset is the means; the dot is the point."""
    from explorer.models import ArticlePlaceManual
    from visuals.corpus import run_story_map

    ArticlePlaceManual.objects.create(
        article=cleaned_story,
        full_name="Linn",
        city="Linn",
        state="MO",
        geoid="2943238",
        geoid_level="place",
        is_point=True,
        added_by="someone@example.org",
    )
    payload = run_story_map(_enriched_spec(dataset), [dataset.slug])
    assert [p["geoid"] for p in payload["points"]] == ["2943238"]
    assert [a["geoid"] for a in payload["areas"]] == ["29151"]


def test_an_article_in_flight_stays_out_however_much_geography_it_has(
    dataset, cleaned_story
):
    """The floor under every filter: a visual drawn from an article the
    pipeline has not finished with would change under the reader. A
    contribution does not lift it."""
    from explorer.models import Article, ArticlePlaceManual
    from visuals.corpus import _base_queryset

    Article.objects.filter(id="cleaned1").update(status="paused")
    ArticlePlaceManual.objects.create(
        article_id="cleaned1",
        full_name="Linn",
        city="Linn",
        state="MO",
        geoid="2943238",
        geoid_level="place",
        is_point=True,
        added_by="someone@example.org",
    )

    ids = set(
        _base_queryset(_enriched_spec(dataset), [dataset.slug]).values_list(
            "id", flat=True
        )
    )
    assert "cleaned1" not in ids


def test_the_status_column_is_not_rewritten(dataset, cleaned_story):
    """`articles.status` is the pipeline's account of what it did, and is
    read by the export, the queue and BigQuery. Editing it to satisfy a
    chart would make every one of them describe something that did not
    happen."""
    from explorer.models import Article, ArticlePlaceManual
    from visuals.corpus import _base_queryset

    ArticlePlaceManual.objects.create(
        article=cleaned_story,
        full_name="Linn",
        city="Linn",
        state="MO",
        geoid="2943238",
        geoid_level="place",
        is_point=True,
        added_by="someone@example.org",
    )
    _base_queryset(_enriched_spec(dataset), [dataset.slug]).count()
    assert Article.objects.get(id="cleaned1").status == "cleaned"
