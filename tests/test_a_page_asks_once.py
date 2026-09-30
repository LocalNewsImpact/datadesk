"""Four findings of the 2026-09-30 review on what a request costs.

- The enrichment grid loaded every article body it joined to.
- The visuals index asked the grants table for every visual in the list.
- A page, the public page and the embed computed the credit line twice,
  the attribution a third time, and re-fetched the snapshot the caller
  already held.
- A pivot with a requirement ran two full counts for its meta.
"""

# ruff: noqa: F401, F811 -- fixtures are imported from the modules that own them.

import inspect

import pytest
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.access import ALL_SCOPES
from accounts.models import DATADESK, Grant
from explorer.models import Article
from tests.test_a_group_counts_its_own_bylines import corpus as byline_corpus
from tests.test_enrichment_grid import enriched_corpus, viewer
from tests.test_publish import admin, published
from tests.test_visuals import author, visual
from visuals.models import Visual

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


# --- the enrichment grid leaves the bodies behind -----------------------------


def test_the_enrichment_grid_does_not_load_article_bodies(
    client, viewer, enriched_corpus
):
    table = Article._meta.db_table
    with CaptureQueriesContext(connections["crawler"]) as ctx:
        response = client.get(reverse("explorer:enrichment"))
    assert response.status_code == 200
    grid = [q["sql"] for q in ctx.captured_queries if "article_enrichment" in q["sql"]]
    assert grid, "the grid ran no query"
    for column in ("text", "raw", "text_excerpt"):
        assert not any(f'"{table}"."{column}"' in q for q in grid), column
    # The title is still there for the row to show.
    assert any(f'"{table}"."title"' in q for q in grid)


# --- the index reads one person's standing once -------------------------------


def _index_queries(client):
    with CaptureQueriesContext(connection) as ctx:
        assert client.get("/visuals/").status_code == 200
    return len(ctx.captured_queries)


def test_the_index_cost_does_not_grow_with_the_list(client, visual, author):
    Grant.objects.create(user=author, app=DATADESK, scope="", role="viewer")
    client.force_login(author)
    visual.datasets = ["mizzou"]
    visual.save(update_fields=["datasets"])
    one = _index_queries(client)
    for i in range(6):
        Visual.objects.create(
            slug=f"more-{i}",
            title=f"More {i}",
            source_kind="inline",
            datasets=["mizzou"],
            created_by=author,
        )
    seven = _index_queries(client)
    assert seven == one, (one, seven)


def test_standing_is_read_when_first_needed_and_kept(author):
    from visuals.services import Standing

    standing = Standing(author)
    with CaptureQueriesContext(connection) as ctx:
        first = standing.readable
        again = standing.readable
        owned = standing.owned
    assert first is again and owned is not None
    # Two answers, two reads -- not one per call.
    assert len(ctx.captured_queries) == 2


def test_the_rules_are_unchanged_with_a_standing(client, visual, author):
    from visuals.services import Standing, may_act_on, visible_to

    standing = Standing(author)
    assert visible_to(author, visual, standing) is visible_to(author, visual)
    assert may_act_on(author, visual, standing) is may_act_on(author, visual)


# --- a page computes each thing once -----------------------------------------


@pytest.fixture
def counted(monkeypatch):
    import visuals.views as views

    calls = {"credit": 0, "attribution": 0}
    credit, attribution = views._credit_line, views._attribution

    def one_credit(visual, attribution=None):
        calls["credit"] += 1
        return credit(visual, attribution)

    def one_attribution(visual):
        calls["attribution"] += 1
        return attribution(visual)

    monkeypatch.setattr(views, "_credit_line", one_credit)
    monkeypatch.setattr(views, "_attribution", one_attribution)
    return calls


def _credited(published):
    published.config = {"credit": "dataset"}
    published.datasets = ["mizzou"]
    published.template = "table"
    published.save(update_fields=["config", "datasets", "template"])
    return published


class TestAPageAsksOnce:
    def test_the_page(self, client, admin, published, counted, crawler_schema):
        client.force_login(admin)
        url = reverse("visuals:page", args=[_credited(published).slug])
        assert client.get(url).status_code == 200
        assert counted == {"credit": 1, "attribution": 1}

    @pytest.mark.urls("datadesk.urls_data")
    def test_the_public_page(self, client, published, counted, crawler_schema):
        # On the data host, by uuid (test_a_visual_can_keep_itself_current).
        url = f"/visuals/{_credited(published).uuid}/"
        assert client.get(url).status_code == 200
        assert counted == {"credit": 1, "attribution": 1}

    def test_the_embed(self, client, published, counted, crawler_schema):
        url = reverse("visuals:embed", kwargs={"slug": _credited(published).slug})
        assert client.get(url).status_code == 200
        assert counted == {"credit": 1, "attribution": 1}

    def test_downloads_describe_the_snapshot_handed_to_them(self, published):
        from visuals.views import _downloads

        assert "snapshot" in inspect.signature(_downloads).parameters
        assert "version" not in inspect.signature(_downloads).parameters
        with CaptureQueriesContext(connection) as ctx:
            files = _downloads(published, False, snapshot=published.pinned_snapshot)
        assert files[0]["url"].endswith("?v=1")
        # The snapshot came in; nothing goes back for it.
        assert not any(
            "visualsnapshot" in q["sql"].lower() for q in ctx.captured_queries
        )


# --- a pivot's requirement is measured in one aggregate -----------------------


def test_a_requirement_is_counted_once_and_distinct(byline_corpus):
    from visuals.corpus import HAS_BYLINE, run_values

    with CaptureQueriesContext(connections["crawler"]) as ctx:
        rows, meta = run_values(
            {"dimensions": ["author"], "measure": "articles"}, ALL_SCOPES
        )
    sql = [q["sql"] for q in ctx.captured_queries]
    filtered = [q for q in sql if "FILTER (WHERE" in q]
    assert len(filtered) == 1, sql
    assert "DISTINCT" in filtered[0]
    assert not any(q.startswith("SELECT COUNT(*)") for q in sql), sql
    assert meta["rows_considered"] == Article.objects.count()
    assert meta["rows_used"] == Article.objects.filter(HAS_BYLINE).count()
    assert rows


def test_without_a_requirement_nothing_is_counted(byline_corpus):
    from visuals.corpus import run_values

    _, meta = run_values(
        {"dimensions": ["publisher_name"], "measure": "articles"}, ALL_SCOPES
    )
    assert meta["rows_considered"] is None and meta["rows_used"] is None
