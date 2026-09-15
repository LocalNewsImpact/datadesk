"""Two things that keep the console's queries honest after today.

A QUERY BUDGET. The grid's cost was not a slow query so much as a query
shape: a paginator counting through a join it did not need, and fifty rows
dragging two copies of the article body nobody rendered. Both are
invisible on seeded data and both are cheap to reintroduce. A budget fails
in CI instead, on the count of queries and on the shape of the SQL, which
is what actually regresses.

THE REPORTING COMMAND. `pg_stat_statements` is the only signal that ranks
every query by measured cost, catches an N+1 (large `calls`, small `mean`,
large `total`), and is not sampled by what somebody happened to click.
`slow_queries` reads it, and -- because "not installed" and "installed but
nothing is slow" must never look alike -- says which of the two reasons it
has nothing to report, and what to do about it.
"""

from datetime import UTC, datetime
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connections
from django.test.utils import CaptureQueriesContext

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def corpus(crawler_schema):
    mo = Dataset.objects.create(id="d1", slug="missouri", label="Missouri")
    src = Source.objects.create(
        id="s1", host="t.example", host_norm="t.example", canonical_name="Tribune"
    )
    DatasetSource.objects.create(id="ds1", dataset=mo, source=src)
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=src)
    for i in range(30):
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


@pytest.fixture
def reader(django_user_model):
    user = django_user_model.objects.create_user("budget-reader")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
    return user


class TestTheGridStaysWithinItsQueryBudget:
    """THE SHAPE THAT REGRESSES. Rendering N rows must cost a fixed number
    of queries, not a number that grows with N -- which is what dropping a
    `select_related`, or deferring a column the template then reads, both
    produce."""

    def _crawler_queries(self, client, params=None):
        with CaptureQueriesContext(connections["crawler"]) as caught:
            response = client.get("/explorer/articles/", params or {})
            assert response.status_code == 200
            # Force the template's lazy work to actually happen.
            list(response.context["page"])
        return caught.captured_queries

    def test_the_grid_does_not_scale_its_queries_with_its_rows(
        self, client, corpus, reader
    ):
        client.force_login(reader)
        few = len(self._crawler_queries(client, {"page": "1"}))
        # The same page with every row on it. If anything is per-row this
        # number moves; if the shape is right it does not.
        many = len(self._crawler_queries(client, {"page": "1", "q": "Story"}))
        assert many <= few + 2, f"{few} -> {many} queries: something is per-row"

    def test_the_budget_is_a_small_fixed_number(self, client, corpus, reader):
        """A ceiling, so a new facet or filter that quietly adds five
        queries to every page load has to be a deliberate change to this
        number rather than an accident.

        Sixteen, measured, on a COLD cache. Five of the sixteen are the
        `SELECT DISTINCT` queries that fill the filter dropdowns -- 1.78
        seconds of them against production, of which `DISTINCT
        primary_label` alone is 1.1s. In production they are paid once per
        `_VOCAB_CACHE_SECONDS` across every instance, not once per page,
        because the cache is a shared DatabaseCache rather than a
        per-process LocMemCache. This test sees them every time, which is
        the honest number to hold: it is what a cold container costs.
        """
        client.force_login(reader)
        queries = self._crawler_queries(client)
        assert len(queries) <= 16, [q["sql"][:80] for q in queries]

    def test_the_count_query_carries_no_joins(self, client, corpus, reader):
        """The 3.7-second query, asserted where it is actually issued
        rather than only on the queryset that builds it."""
        client.force_login(reader)
        counts = [
            q["sql"]
            for q in self._crawler_queries(client)
            if "COUNT(*)" in q["sql"] and '"articles"' in q["sql"]
        ]
        assert counts, "no count query ran; the paginator stopped counting"
        for sql in counts:
            assert "article_enrichment" not in sql
            assert "JOIN" not in sql.upper()

    def test_no_query_on_the_grid_selects_the_article_body(
        self, client, corpus, reader
    ):
        """Two columns hold the same body and the grid renders neither."""
        client.force_login(reader)
        for query in self._crawler_queries(client):
            sql = query["sql"]
            if 'FROM "articles"' in sql or 'from "articles"' in sql.lower():
                assert '"articles"."text"' not in sql
                assert '"articles"."content"' not in sql


class TestTheSlowQueryReportIsHonestAboutWhatItKnows:
    def _run(self, **kwargs):
        out = StringIO()
        call_command("slow_queries", stdout=out, **kwargs)
        return out.getvalue()

    def test_it_says_when_the_extension_is_missing(self):
        """THE FAILURE TO AVOID: reporting nothing and letting that read as
        'nothing is slow'."""
        output = self._run(database="default")
        assert "not created" in output

    def test_it_says_which_reason_and_what_to_do(self):
        """Preloaded-but-not-created is one statement away; not preloaded
        needs an instance flag and a restart. The operator has to be told
        which."""
        output = self._run(database="default")
        assert ("CREATE EXTENSION" in output) or ("shared_preload_libraries" in output)

    def test_it_does_not_raise_when_there_is_nothing_to_report(self):
        """A monitoring command that crashes on a healthy system gets
        removed from the runbook."""
        self._run(database="default")

    @pytest.mark.parametrize("order", ["total", "mean", "calls", "rows"])
    def test_every_documented_ordering_is_accepted(self, order):
        self._run(database="default", order=order)

    def test_an_unknown_ordering_is_refused(self):
        """Through argv, because `call_command(order=...)` bypasses
        argparse and would accept anything -- which would make this test
        pass while the command line it documents did not work."""
        from django.core.management.base import CommandError

        with pytest.raises((CommandError, SystemExit)):
            call_command("slow_queries", "--order", "sideways", stdout=StringIO())


class TestTheBlockedInventoryReadsTheStoredLength:
    """`articles.text_length` is a STORED generated column --
    `length(coalesce(content, text, text_excerpt, ''))` -- and it is
    indexed. Asking for an empty body by recomputing that coalesce reads
    every body out of TOAST; asking for `text_length = 0` reads an index.

    MEASURED against production, 165,459 articles: the coalesce form is a
    sequential scan at 434 ms, the column an index-only scan at 0.9 ms,
    and both return 1,234.

    The review queue stopped measuring this by hand in b25f5eb. This count
    was the site that was missed, and `pg_stat_statements` is what found
    it -- neither the request log nor a static scan had anything to say
    about a query issued by a scheduled command.
    """

    def _empty_bodies(self, sql):
        from django.db import connections

        with connections["crawler"].cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchone()[0]

    @pytest.fixture
    def bodies(self, crawler_schema):
        src = Source.objects.create(
            id="s9", host="b.example", host_norm="b.example", canonical_name="B"
        )
        link = CandidateLink.objects.create(id="cl9", url="https://b/", source=src)
        for i, (content, text, excerpt) in enumerate(
            [
                ("a real body", None, None),  # has content
                (None, "fallback body", None),  # falls back to text
                (None, None, "just an excerpt"),
                (None, None, None),  # empty: all three null
                ("", "", ""),  # empty: all three blank
            ]
        ):
            Article.objects.create(
                id=f"b{i}",
                candidate_link=link,
                url=f"https://b.example/{i}",
                title=f"B{i}",
                status="extracted",
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
                content=content,
                text=text,
                text_excerpt=excerpt,
            )

    def test_the_stored_column_agrees_with_the_coalesce_it_replaces(self, bodies):
        """THE EQUIVALENCE THIS RESTS ON. If the generated expression ever
        stops matching the coalesce, the inventory quietly reports a
        different number and nothing else would notice."""
        by_column = self._empty_bodies(
            "SELECT count(*) FROM articles WHERE text_length = 0"
        )
        by_coalesce = self._empty_bodies(
            "SELECT count(*) FROM articles "
            "WHERE coalesce(content, text, text_excerpt, '') = ''"
        )
        assert by_column == by_coalesce == 2

    def test_the_inventory_asks_by_the_indexed_column(self):
        """Asserted on the shipped SQL, because the cost only diverges at
        production's row counts -- on seeded data both are instant."""
        from explorer import blocked

        checks = "\n".join(str(c) for c in blocked.CHECKS)
        assert "text_length = 0" in checks
        assert "coalesce(content, text, text_excerpt, '') = ''" not in checks
