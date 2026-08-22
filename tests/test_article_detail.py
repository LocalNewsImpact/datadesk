"""Article detail: the full enrichment record an operator judges.

The screen has to distinguish two assertions the schema keeps apart — the
central-geography claim (`point_geoid`, one location, most precise rung
resolved) and the mention list (`geoids`, which never repeats the claim) —
and to show the extracted people, organizations and places.
"""

from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import Group, User

from explorer.models import (
    Article,
    ArticleEnrichment,
    ArticleGeoid,
    ArticleOrganization,
    ArticlePerson,
    ArticlePlace,
    CandidateLink,
    Source,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="viewer"))
    client.force_login(user)
    return user


@pytest.fixture
def article(crawler_schema):
    source = Source.objects.create(
        id="s1",
        host="tribune.example",
        host_norm="tribune.example",
        canonical_name="Columbia Tribune",
    )
    link = CandidateLink.objects.create(id="cl1", url="https://t/1", source=source)
    return Article.objects.create(
        id="a1",
        candidate_link=link,
        url="https://tribune.example/1",
        title="Council votes on the levy",
        author="Jane Reporter",
        status="enriched",
        wire_check_status="complete",
        created_at=datetime(2026, 3, 4, tzinfo=UTC),
        publish_date=datetime(2026, 3, 4, tzinfo=UTC),
        content="Officials met Tuesday to discuss the levy.",
        primary_label="government",
        primary_label_confidence=0.91,
    )


def _get(client, article_id="a1"):
    return client.get(f"/explorer/articles/{article_id}/").content.decode()


def test_every_dimension_shows_value_confidence_and_rationale(client, viewer, article):
    ArticleEnrichment.objects.create(
        article=article,
        scope="city_municipality",
        scope_confidence=0.92,
        subject="government",
        subject_confidence=0.88,
        topic="taxes",
        topic_confidence=0.71,
        format="news_report",
        format_confidence=0.95,
        timeframe="current",
        timeframe_confidence=0.8,
        user_need="know",
        user_need_confidence=0.77,
        rationales={
            "scope": "The story names a single city council.",
            "user_need": "Reports a decision without analysis.",
            "gate": "Body text exceeded the minimum length.",
        },
    )
    content = _get(client, article.id)
    for value in (
        "city_municipality",
        "government",
        "taxes",
        "news_report",
        "timeframe",
        "user_need",
    ):
        assert value in content
    for confidence in ("0.92", "0.88", "0.71", "0.95", "0.77"):
        assert confidence in content
    assert "single city council" in content
    assert "Reports a decision without analysis." in content
    # A rationale under a non-dimension key still reaches the screen.
    assert "Body text exceeded the minimum length." in content


def test_central_claim_shows_basis_rung_and_zip(client, viewer, article):
    ArticleEnrichment.objects.create(
        article=article,
        point_place="Columbia",
        point_geoid="2915670",
        point_geoid_level="place",
        point_method="focus_model",
        point_zcta="65201",
        point_lat=38.9517,
        point_lon=-92.3341,
        geoids='["29019", "29"]',
    )
    content = _get(client, article.id)
    assert "Columbia" in content
    assert "2915670" in content
    assert "place" in content
    assert "focus_model" in content
    assert "65201" in content
    # The mentions become rows of the one geography table, each resolved
    # to the place its code stands for.
    assert "29019" in content
    assert "Boone County, MO" in content
    assert "Missouri" in content
    assert "claim" in content
    assert "mention" in content
    # The geography is stated once, not in three tables.
    assert "Mentioned FIPS" not in content
    assert "Geography rows" not in content


def test_publication_place_assumed_is_shown_as_the_basis(client, viewer, article):
    ArticleEnrichment.objects.create(
        article=article,
        point_place="Columbia",
        point_geoid="2915670",
        point_geoid_level="place",
        point_method="publication_place_assumed",
    )
    assert "publication_place_assumed" in _get(client, article.id)


def test_missing_claim_shows_the_geo_skip_reason(client, viewer, article):
    ArticleEnrichment.objects.create(
        article=article,
        geo_skip_reason="regional_uses_place_set",
    )
    content = _get(client, article.id)
    assert "No central-geography claim" in content
    assert "regional_uses_place_set" in content


def test_geography_table_flags_the_claim_against_the_mentions(client, viewer, article):
    ArticleEnrichment.objects.create(
        article=article, point_geoid="2915670", point_place="Columbia"
    )
    ArticleGeoid.objects.create(
        article=article,
        geoid="2915670",
        geoid_level="place",
        is_primary=True,
        source="focus_model",
    )
    ArticleGeoid.objects.create(
        article=article,
        geoid="29019",
        geoid_level="county",
        is_primary=False,
        source="place_mention",
    )
    content = _get(client, article.id)
    assert "claim" in content
    assert "mention" in content
    assert "place_mention" in content
    # Codes and names travel together.
    assert "Columbia, MO" in content
    assert "Boone County, MO" in content
    # The claim leads.
    assert content.index("2915670") < content.index("29019")


def test_entities_are_listed(client, viewer, article):
    ArticleEnrichment.objects.create(article=article)
    ArticlePerson.objects.create(
        article=article,
        name="Barbara Buffaloe",
        title="Mayor",
        affiliation="City of Columbia",
        role_in_story="subject",
        public_figure=True,
        mention_count=4,
    )
    ArticleOrganization.objects.create(
        article=article,
        name="Columbia City Council",
        org_type="government",
        boundary="municipal",
        mention_count=6,
    )
    ArticlePlace.objects.create(
        article=article,
        full_name="Daniel Boone City Building",
        place_type="civic",
        city="Columbia",
        county="Boone",
        state="MO",
        geoid="2915670",
        geoid_level="place",
    )
    content = _get(client, article.id)
    assert "Barbara Buffaloe" in content
    assert "Mayor" in content
    assert "Columbia City Council" in content
    # An extracted venue rides on the geography row its code matches,
    # rather than forming a second table.
    assert "Daniel Boone City Building" in content


def test_entity_sections_say_so_when_empty(client, viewer, article):
    ArticleEnrichment.objects.create(article=article)
    content = _get(client, article.id)
    assert content.count("None extracted.") == 2
    assert "No geography recorded" in content


def test_geoids_parsing_tolerates_a_comma_separated_column(client, viewer, article):
    ArticleEnrichment.objects.create(article=article, geoids="29019,29")
    assert "29019" in _get(client, article.id)


def test_geoids_parsing_survives_an_unreadable_value(client, viewer, article):
    ArticleEnrichment.objects.create(article=article, geoids="{not json")
    assert client.get(f"/explorer/articles/{article.id}/").status_code == 200


# --- wire: assumed unless it is actually wire -------------------------------


def test_local_articles_show_no_wire_row(client, viewer, article):
    """An article that reached enrichment already passed the check, so
    the field carries no information. 'local' is a passing value too, and
    rendering it produced "Wire: local", which reads as its opposite."""
    ArticleEnrichment.objects.create(article=article)
    content = _get(client, article.id)
    assert "Wire" not in content
    assert "wire_check_status" not in content

    article.wire_check_status = "local"
    article.save()
    assert "Wire" not in _get(client, article.id)


def test_a_syndicated_article_says_so_with_its_services(client, viewer, article):
    ArticleEnrichment.objects.create(article=article)
    article.wire_check_status = "wire"
    article.wire = ["Associated Press", "Reuters"]
    article.save()
    content = _get(client, article.id)
    assert "Wire: Yes" in content
    assert "Associated Press, Reuters" in content


def test_wire_findings_alone_are_enough(client, viewer, article):
    """A non-empty wire column is the finding, whatever the status says."""
    ArticleEnrichment.objects.create(article=article)
    article.wire = [{"service": "Associated Press"}]
    article.save()
    content = _get(client, article.id)
    assert "Wire: Yes" in content
    assert "Associated Press" in content


def test_wire_column_shapes_are_tolerated(client, viewer, article):
    from explorer.models import Article

    fresh = Article.objects.get(id=article.id)
    fresh.wire = ["AP"]
    assert fresh.wire_services() == ["AP"]
    fresh.wire = [{"name": "Reuters"}]
    assert fresh.wire_services() == ["Reuters"]
    fresh.wire = "AP"
    assert fresh.wire_services() == ["AP"]
    fresh.wire = []
    assert fresh.wire_services() == []
    fresh.wire = None
    assert fresh.wire_services() == []
    fresh.wire = 17
    assert fresh.wire_services() == []


def test_raw_status_enums_are_not_shown_bare(client, viewer, article):
    """A status is displayed as a human label, with the raw value kept in
    a title attribute where it still helps when debugging."""
    ArticleEnrichment.objects.create(article=article)
    content = _get(client, article.id)
    assert 'title="enriched"' in content
    assert ">Enriched<" in content
