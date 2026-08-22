"""The landing dashboard: what the corpus holds, and the degraded case."""

from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import Group, User

from explorer.models import (
    Article,
    ArticleEnrichment,
    CandidateLink,
    Dataset,
    DatasetSource,
    Source,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="viewer"))
    client.force_login(user)
    return user


def _article(article_id, link, status, **overrides):
    fields = {
        "id": article_id,
        "candidate_link": link,
        "url": f"https://example.org/{article_id}",
        "title": f"Story {article_id}",
        "status": status,
        "wire_check_status": "complete",
        "created_at": datetime(2026, 3, 1, tzinfo=UTC),
        "publish_date": datetime(2026, 3, 1, tzinfo=UTC),
        "content": "text",
    }
    fields.update(overrides)
    return Article.objects.create(**fields)


@pytest.fixture
def corpus(crawler_schema):
    """Ten articles: four enriched, two skipped, two labeled, one
    not_article, one out_of_scope."""
    mo = Dataset.objects.create(id="d1", slug="missouri", label="Missouri")
    lv = Dataset.objects.create(id="d2", slug="lehigh", label="Lehigh Valley")
    tribune = Source.objects.create(
        id="s1", host="tribune.example", host_norm="tribune.example"
    )
    DatasetSource.objects.create(id="ds1", dataset=mo, source=tribune)
    DatasetSource.objects.create(id="ds2", dataset=lv, source=tribune)
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=tribune)

    for i in range(4):
        article = _article(f"e{i}", link, "enriched")
        ArticleEnrichment.objects.create(
            article=article,
            cost_usd=0.01,
            point_geoid="2915670" if i < 3 else None,
        )
    for i in range(2):
        article = _article(f"s{i}", link, "enrichment_skipped")
        ArticleEnrichment.objects.create(article=article, skip_reason="paywall_stub")
    _article("l0", link, "labeled")
    _article("l1", link, "labeled")
    _article("n0", link, "not_article", content="")
    _article("o0", link, "out_of_scope")


def _content(client):
    return client.get("/").content.decode()


def test_status_counts(client, viewer, corpus):
    from explorer.dashboard import corpus_summary

    summary = corpus_summary()
    counts = {row["status"]: row["articles"] for row in summary["by_status"]}
    assert counts == {
        "enriched": 4,
        "enrichment_skipped": 2,
        "labeled": 2,
        "not_article": 1,
        "out_of_scope": 1,
    }
    assert summary["total"] == 10


def test_known_statuses_lead_in_pipeline_order(client, viewer, corpus):
    from explorer.dashboard import corpus_summary

    order = [row["status"] for row in corpus_summary()["by_status"]]
    assert order == [
        "enriched",
        "enrichment_skipped",
        "labeled",
        "not_article",
        "out_of_scope",
    ]


def test_an_unknown_status_still_appears(client, viewer, corpus, crawler_schema):
    """The vocabulary is the pipeline's; a status this code does not name
    must not vanish from the dashboard."""
    from explorer.dashboard import corpus_summary

    link = CandidateLink.objects.get(id="cl1")
    _article("x0", link, "some_future_status")
    statuses = [row["status"] for row in corpus_summary()["by_status"]]
    assert statuses[-1] == "some_future_status"


def test_enrichment_coverage(client, viewer, corpus):
    from explorer.dashboard import corpus_summary

    summary = corpus_summary()
    assert summary["enriched"] == 4
    assert summary["coverage"] == pytest.approx(40.0)
    assert summary["exported_unenriched"] == 2
    # Six enrichment rows: four enriched plus two carrying a skip reason.
    assert summary["enrichment_rows"] == 6
    assert summary["with_claim"] == 3


def test_review_backlog_counts_the_queue(client, viewer, corpus):
    from explorer.dashboard import corpus_summary

    # Two paywall stubs, one not_article, one out_of_scope.
    assert corpus_summary()["flagged"] == 4


def test_dashboard_renders_the_figures(client, viewer, corpus):
    content = _content(client)
    assert "Articles by status" in content
    assert "enrichment_skipped" in content
    assert "40.0%" in content
    assert "Awaiting review" in content


def test_cost_totals_by_dataset(client, viewer, corpus):
    content = _content(client)
    assert "Missouri" in content
    assert "Lehigh Valley" in content
    # Four enriched articles at $0.01 each, on both datasets' sources.
    assert "$0.04" in content


def test_datasets_table_joins_counts_to_costs():
    from explorer.dashboard import datasets_table

    table = datasets_table(
        [
            {"slug": "missouri", "label": "Missouri", "articles": 10},
            {"slug": "vt", "label": "Vermont", "articles": 3},
        ],
        {"by_dataset": [{"slug": "missouri", "cost": 1.5, "articles": 4}]},
    )
    assert table[0] == {
        "slug": "missouri",
        "label": "Missouri",
        "articles": 10,
        "enriched": 4,
        "cost": 1.5,
    }
    # A dataset with no recorded cost still lists, at zero.
    assert table[1]["cost"] == 0
    assert table[1]["enriched"] == 0


def test_datasets_table_without_costs():
    from explorer.dashboard import datasets_table

    table = datasets_table([{"slug": "a", "label": "A", "articles": 1}], None)
    assert table[0]["cost"] == 0


def test_navigation_reaches_every_section(client, viewer, corpus):
    content = _content(client)
    for url in (
        "/explorer/articles/",
        "/explorer/enrichment/",
        "/explorer/costs/",
        "/review/queue/",
    ):
        assert url in content


def test_status_rows_link_into_the_filtered_grid(client, viewer, corpus):
    assert "/explorer/articles/?status=enriched" in _content(client)


def test_degrades_without_the_crawler_database(client, viewer):
    from explorer.dashboard import corpus_summary

    assert corpus_summary() is None
    content = _content(client)
    assert "not connected" in content
    assert "not configured" in content
    # The banner replaces the figures rather than showing zeros.
    assert "Articles by status" not in content


def test_sections_are_reachable_without_the_crawler_database(client, viewer):
    """A missing crawler connection must not hide the rest of the app."""
    content = _content(client)
    assert "/explorer/articles/" in content
    assert "/review/queue/" in content


def test_a_user_without_a_role_sees_no_corpus(client, corpus):
    email = "norole@localnewsimpact.org"
    client.force_login(User.objects.create_user(username=email, email=email))
    content = _content(client)
    assert "none assigned" in content
    assert "Articles by status" not in content
    assert "Missouri" not in content
