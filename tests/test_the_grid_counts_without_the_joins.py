"""The articles grid spent 3.7 seconds counting what it took 99 ms to fetch.

Django's `Paginator` calls `.count()` on the queryset it is handed.
`.count()` drops `select_related`, but an `annotate(F("enrichment__x"))`
survives into the count as a subquery -- and that join turns a parallel
index-only scan into a sequential scan of a 1.5 GB table.

MEASURED against production on 2026-09-15, 165,459 articles / 262,041
candidate links:

    count as the grid built it      3,703 ms cold / 359 ms warm
    count without the enrichment      ~200-360 ms
    count with no joins at all          192 ms   (index-only scan)
    the 50-row page itself               99 ms

So the count cost up to 37x the query it was counting, on every page load,
and it held one of eight gunicorn threads for the duration. At twelve
concurrent users that is the difference between a responsive console and a
queue -- including for people who only wanted to look at a chart.

The annotations exist to RENDER rows, not to select them: article to
enrichment is one-to-one, so joining it cannot change how many rows there
are. Filters that narrow THROUGH enrichment are a different matter and
must stay in the count; `TestFiltersThroughEnrichmentStillCount` is what
keeps that honest.

These assert on the SQL and on query counts rather than on a stopwatch. A
seeded table is small enough that both plans look instant; the shapes only
diverge at production's row counts, and the shape is what went wrong.
"""

from datetime import UTC, datetime

import pytest

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from explorer.views import GridPaginator, _filtered_articles

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/explorer/articles/"


@pytest.fixture
def corpus(crawler_schema):
    mo = Dataset.objects.create(id="d1", slug="missouri", label="Missouri")
    src = Source.objects.create(
        id="s1", host="t.example", host_norm="t.example", canonical_name="Tribune"
    )
    DatasetSource.objects.create(id="ds1", dataset=mo, source=src)
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=src)
    for i in range(6):
        Article.objects.create(
            id=f"a{i}",
            candidate_link=link,
            url=f"https://example.org/{i}",
            title=f"Story {i}",
            status="labeled",
            wire_check_status="complete",
            created_at=datetime(2026, 3, 1, tzinfo=UTC),
            publish_date=datetime(2026, 3, 1, tzinfo=UTC),
            text="body " * 400,
            content="body " * 400,
        )
    return link


@pytest.fixture
def reader(django_user_model):
    user = django_user_model.objects.create_user("grid-reader")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
    return user


def _count_sql(params=None, user=None):
    qs = _filtered_articles(params or {}, user, annotated=False)
    return str(qs.query).lower()


def _display_sql(params=None, user=None):
    return str(_filtered_articles(params or {}, user).query).lower()


class TestTheCountDoesNotJoinWhatItDoesNotNeed:
    def test_the_count_queryset_has_no_enrichment_join(self, corpus, reader):
        """THE REGRESSION. This join is what made the count a sequential
        scan of the whole table."""
        assert "article_enrichment" not in _count_sql(user=reader)

    def test_the_display_queryset_still_has_it(self, corpus, reader):
        """The grid renders scope and the central-FIPS claim from those
        annotations. Removing the join from the count must not remove it
        from the rows."""
        assert "article_enrichment" in _display_sql(user=reader)

    def test_the_two_agree_on_how_many_rows_there_are(self, corpus, reader):
        """The whole argument for stripping the annotations is that they
        cannot change the count. If that were ever false this fails."""
        annotated = _filtered_articles({}, reader)
        plain = _filtered_articles({}, reader, annotated=False)
        assert annotated.count() == plain.count() == 6


class TestFiltersThroughEnrichmentStillCount:
    """A filter on `enrichment__` narrows the rows, so its join is part of
    the question. Stripping it would make the count disagree with the
    page -- a reader would see "6 results" over four rows."""

    @pytest.mark.parametrize(
        "params",
        [
            {"scope": "local"},
            {"geo_skip": "no_place"},
            {"fips": "yes"},
            {"fips": "no"},
        ],
    )
    def test_the_join_survives_for_a_filter_that_needs_it(self, corpus, reader, params):
        assert "article_enrichment" in _count_sql(params, reader)


class TestTheGridDoesNotSelectBodyText:
    """`text` and `content` hold the same article -- 2,044 bytes apiece on
    average, and the crawler keeps `text` only for compatibility --
    plus 466 in `text_excerpt`. The grid renders none of them. Row width in
    the production plan falls from 2,227 bytes to 242."""

    @pytest.mark.parametrize("column", ["text", "content", "text_excerpt"])
    def test_the_big_columns_are_deferred(self, corpus, reader, column):
        sql = _display_sql(user=reader)
        assert f'"{column}"' not in sql

    def test_the_rendered_columns_are_still_there(self, corpus, reader):
        sql = _display_sql(user=reader)
        for column in ("title", "status", "publish_date", "author"):
            assert f'"{column}"' in sql

    def test_a_deferred_column_is_not_fetched_per_row(self, corpus, reader):
        """A defer that the template then touches is worse than no defer:
        it becomes one query per row. Nothing in the grid path may read
        them."""
        from django.db import connections
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connections["crawler"]) as caught:
            rows = list(_filtered_articles({}, reader)[:6])
            [(r.title, r.status) for r in rows]
        assert len(caught) == 1, [q["sql"][:90] for q in caught.captured_queries]


class TestThePaginatorUsesTheCountQueryset:
    def test_it_counts_through_the_queryset_it_was_given(self, corpus, reader):
        plain = _filtered_articles({}, reader, annotated=False)
        paginator = GridPaginator(
            _filtered_articles({}, reader), 2, count_queryset=plain
        )
        assert paginator.count == 6
        assert paginator.num_pages == 3

    def test_without_one_it_behaves_like_a_plain_paginator(self, corpus, reader):
        """The fallback has to keep working, or an unrelated caller that
        constructs one without the keyword silently counts nothing."""
        paginator = GridPaginator(_filtered_articles({}, reader), 2)
        assert paginator.count == 6


class TestThePageStillWorks:
    def test_the_grid_renders_and_counts_right(self, client, corpus, reader):
        client.force_login(reader)
        response = client.get(URL)
        assert response.status_code == 200
        assert response.context["page"].paginator.count == 6

    def test_the_second_page_is_reachable(self, client, corpus, reader):
        client.force_login(reader)
        response = client.get(URL, {"page": "2"})
        assert response.status_code == 200
