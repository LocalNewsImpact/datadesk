"""An export carries the AI enrichment, and Export sits beside the count.

Asked for 2026-10-15: the export offered the CIN label and nothing the
enrichment wrote -- not the geographic classification, the central geography,
the people or the organizations. And the Export link sat in the filter form,
where it carried the filters the page loaded with rather than the ones the
reader had since chosen.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from explorer.models import (
    Article,
    ArticleEnrichment,
    ArticleOrganization,
    ArticlePerson,
    ArticlePlace,
)
from tests.test_import_export import corpus, viewer  # noqa: F401 - fixtures

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

ENRICHMENT = [
    "alternate_label",
    "scope",
    "scope_confidence",
    "point_place",
    "point_geoid",
    "point_lat",
    "places",
    "geoids",
    "people",
    "organizations",
]


@pytest.fixture
def enriched(corpus):  # noqa: F811 - the imported fixture
    Article.objects.filter(id="a1").update(
        alternate_label="Civic information", alternate_label_confidence=0.04
    )
    article = Article.objects.get(id="a1")
    ArticleEnrichment.objects.create(
        article=article,
        enriched_at=datetime(2026, 3, 2, tzinfo=UTC),
        scope="city_municipality",
        scope_confidence=0.91,
        point_place="Yakima",
        point_geoid="5380010",
        point_geoid_level="place",
        point_lat=46.6,
        point_lon=-120.5,
        geoids='["5380010", "53077"]',
    )
    ArticlePerson.objects.create(
        article=article,
        name="Jane Doe",
        title="Mayor",
        affiliation="City of Yakima",
        mention_count=1,
    )
    ArticlePerson.objects.create(article=article, name="John Roe", mention_count=4)
    ArticleOrganization.objects.create(
        article=article, name="Yakima County", mention_count=2
    )
    ArticlePlace.objects.create(article=article, full_name="Yakima, WA")
    ArticlePlace.objects.create(article=article, full_name="Yakima, WA")


def _rows(client, columns):
    response = client.post(
        "/review/export/", {"columns": columns, "f_status": "labeled"}
    )
    lines = response.content.decode("utf-8-sig").strip().split("\n")
    import csv

    reader = list(csv.reader(lines))
    header, body = reader[0], reader[1:]
    return {row[0]: dict(zip(header, row, strict=True)) for row in body}


def test_the_enrichment_columns_are_offered(client, viewer):  # noqa: F811
    page = client.get("/review/export/").content.decode()
    for column in ENRICHMENT + ["user_need", "point_method", "is_news_content"]:
        assert f'value="{column}"' in page


def test_an_enriched_article_exports_its_fields(client, viewer, enriched):  # noqa: F811
    row = _rows(client, ENRICHMENT)["a1"]
    assert row["alternate_label"] == "Civic information"
    assert row["scope"] == "city_municipality"
    assert row["scope_confidence"] == "0.91"
    assert row["point_place"] == "Yakima"
    assert row["point_geoid"] == "5380010"
    assert row["point_lat"] == "46.6"
    assert row["geoids"] == "5380010; 53077"
    assert row["places"] == "Yakima, WA"


def test_people_and_organizations_most_mentioned_first(
    client, viewer, enriched  # noqa: F811
):
    row = _rows(client, ["people", "organizations"])["a1"]
    assert row["people"] == "John Roe; Jane Doe (Mayor, City of Yakima)"
    assert row["organizations"] == "Yakima County"


def test_an_article_never_enriched_exports_blanks(
    client, viewer, enriched  # noqa: F811
):
    row = _rows(client, ENRICHMENT)["a2"]
    assert all(row[column] == "" for column in ENRICHMENT)


def test_the_related_lists_cost_one_query_each(
    client, viewer, enriched, django_assert_max_num_queries  # noqa: F811
):
    """Not one query per article per column: enrichment joins, and each
    chosen list is one prefetch."""
    with django_assert_max_num_queries(12):
        _rows(client, ENRICHMENT)


class TestTheExportLinkSitsBesideTheCount:
    def test_it_is_in_the_results_head(self):
        results = Path("templates/explorer/_articles_results.html").read_text()
        head = results[results.index('<div class="results-head">') :]
        head = head[: head.index("</div>")]
        assert 'class="result-count"' in head
        assert "review:export" in head

    def test_it_left_the_filter_form(self):
        page = Path("templates/explorer/articles.html").read_text()
        assert "review:export" not in page

    def test_it_is_right_justified(self):
        css = Path("static/css/datadesk.css").read_text()
        block = css[css.index(".results-head {") :]
        block = block[: block.index("}")]
        assert "justify-content: space-between" in block
