"""The articles grid: filters, pagination, access, degraded mode.

Fixture data goes through the unmanaged ORM models — in tests the crawler
alias is a writable Postgres database, which also exercises
CrawlerRouter's routing;
in production the same write would be refused by Postgres (datadesk_ro).
"""

from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

URL = "/explorer/articles/"


def _article(i, source_link, **overrides):
    fields = {
        "id": f"a{i}",
        "candidate_link": source_link,
        "url": f"https://example.org/{i}",
        "title": f"Story {i}",
        "status": "labeled",
        "wire_check_status": "complete",
        "created_at": datetime(2026, 3, 1, tzinfo=UTC),
        "publish_date": datetime(2026, 3, 1, tzinfo=UTC),
        "primary_label": "news",
        "primary_label_confidence": 0.9,
    }
    fields.update(overrides)
    return Article.objects.create(**fields)


@pytest.fixture
def corpus(crawler_schema):
    """Two datasets, two publishers, four articles with contrasts."""
    mo = Dataset.objects.create(id="d1", slug="missouri", label="Missouri")
    lv = Dataset.objects.create(id="d2", slug="lehigh", label="Lehigh Valley")
    tribune = Source.objects.create(
        id="s1",
        host="tribune.example",
        host_norm="tribune.example",
        canonical_name="Tribune",
    )
    herald = Source.objects.create(
        id="s2",
        host="herald.example",
        host_norm="herald.example",
        canonical_name="Herald",
    )
    DatasetSource.objects.create(id="ds1", dataset=mo, source=tribune)
    DatasetSource.objects.create(id="ds2", dataset=lv, source=herald)
    cl1 = CandidateLink.objects.create(id="cl1", url="https://t/", source=tribune)
    cl2 = CandidateLink.objects.create(id="cl2", url="https://h/", source=herald)

    _article(1, cl1)
    _article(
        2,
        cl1,
        status="extracted",
        wire_check_status="pending",
        primary_label_confidence=0.4,
        publish_date=datetime(2026, 1, 15, tzinfo=UTC),
    )
    _article(3, cl2, primary_label="sports")
    _article(
        4,
        cl2,
        title="Council votes on budget",
        primary_label=None,
        primary_label_confidence=None,
    )


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="viewer")
    client.force_login(user)
    return user


def _titles(response):
    content = response.content.decode()
    return [f"Story {i}" for i in range(1, 5) if f"Story {i}" in content]


def test_anonymous_is_sent_to_sign_in(client):
    response = client.get(URL)
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


def test_no_role_is_forbidden(client):
    client.force_login(User.objects.create_user("norole", email="n@example.org"))
    assert client.get(URL).status_code == 403


def test_grid_lists_the_corpus(client, viewer, corpus):
    response = client.get(URL)
    assert response.status_code == 200
    assert _titles(response) == ["Story 1", "Story 2", "Story 3"]
    assert "Council votes on budget" in response.content.decode()


def test_dataset_filter_follows_membership(client, viewer, corpus):
    response = client.get(URL, {"dataset": "missouri"})
    assert _titles(response) == ["Story 1", "Story 2"]


def test_status_and_wire_filters(client, viewer, corpus):
    assert _titles(client.get(URL, {"status": "extracted"})) == ["Story 2"]
    assert _titles(client.get(URL, {"wire": "pending"})) == ["Story 2"]


def test_publisher_search(client, viewer, corpus):
    response = client.get(URL, {"publisher": "Herald"})
    assert _titles(response) == ["Story 3"]
    assert "Council votes on budget" in response.content.decode()


def test_label_and_confidence_filters(client, viewer, corpus):
    assert _titles(client.get(URL, {"label": "sports"})) == ["Story 3"]
    assert _titles(client.get(URL, {"conf_max": "0.5"})) == ["Story 2"]
    assert _titles(client.get(URL, {"conf_min": "0.5", "label": "news"})) == ["Story 1"]


def test_date_range_filter(client, viewer, corpus):
    response = client.get(URL, {"from": "2026-01-01", "to": "2026-01-31"})
    assert _titles(response) == ["Story 2"]


def test_title_search(client, viewer, corpus):
    response = client.get(URL, {"q": "council"})
    assert _titles(response) == []
    assert "Council votes on budget" in response.content.decode()


def test_filter_vocab_comes_from_the_data(client, viewer, corpus):
    content = client.get(URL).content.decode()
    for value in ("labeled", "extracted", "pending", "complete", "sports"):
        assert value in content
    assert "Missouri" in content and "Lehigh Valley" in content


def test_pagination(client, viewer, crawler_schema):
    source = Source.objects.create(id="s1", host="t.example", host_norm="t.example")
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=source)
    for i in range(60):
        _article(i, link)
    first = client.get(URL)
    assert "page 1 of 2" in first.content.decode()
    second = client.get(URL, {"page": "2"})
    assert "page 2 of 2" in second.content.decode()


def test_htmx_request_gets_only_the_results_fragment(client, viewer, corpus):
    response = client.get(URL, HTTP_HX_REQUEST="true")
    content = response.content.decode()
    assert "Story 1" in content
    assert "<html" not in content
    assert "filter-bar" not in content


def test_degrades_without_crawler_tables(client, viewer):
    response = client.get(URL)
    assert response.status_code == 200
    assert "not connected" in response.content.decode()


# --- article detail ---------------------------------------------------------


@pytest.fixture
def enriched_article(crawler_schema):
    from explorer.models import ArticleEnrichment

    source = Source.objects.create(
        id="s1",
        host="tribune.example",
        host_norm="tribune.example",
        canonical_name="Tribune",
    )
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=source)
    article = _article(
        1, link, content="Officials met Tuesday to discuss the levy.\n\nIt passed."
    )
    ArticleEnrichment.objects.create(
        article=article,
        scope="city_municipality",
        scope_confidence=0.92,
        subject="government",
        subject_confidence=0.88,
        cost_usd=0.0042,
        point_place="Columbia",
        point_geoid="2915670",
        point_geoid_level="place",
        rationales={"scope": "The story names a single city council."},
    )
    return article


def test_detail_shows_text_and_enrichment_side_by_side(
    client, viewer, enriched_article
):
    response = client.get(f"/explorer/articles/{enriched_article.id}/")
    content = response.content.decode()
    assert response.status_code == 200
    assert "Officials met Tuesday" in content
    assert "city_municipality" in content
    assert "0.92" in content
    assert "2915670" in content
    assert "0.0042" in content
    assert "single city council" in content


def test_detail_without_enrichment_says_so(client, viewer, crawler_schema):
    source = Source.objects.create(id="s1", host="t.example", host_norm="t.example")
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=source)
    _article(1, link)
    response = client.get("/explorer/articles/a1/")
    assert response.status_code == 200
    assert "No enrichment record" in response.content.decode()


def test_detail_unknown_article_is_404(client, viewer, crawler_schema):
    assert client.get("/explorer/articles/nope/").status_code == 404


def test_detail_without_crawler_db_is_404(client, viewer):
    assert client.get("/explorer/articles/a1/").status_code == 404


def test_grid_titles_link_to_detail(client, viewer, corpus):
    content = client.get(URL).content.decode()
    assert "/explorer/articles/a1/" in content


# --- enrichment facets on the articles grid ---------------------------------


@pytest.fixture
def geo_corpus(crawler_schema):
    """Four articles with contrasting enrichment geography."""
    from explorer.models import ArticleEnrichment

    source = Source.objects.create(
        id="s1",
        host="tribune.example",
        host_norm="tribune.example",
        canonical_name="Tribune",
    )
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=source)

    claimed = _article(1, link, author="Jane Reporter")
    ArticleEnrichment.objects.create(
        article=claimed,
        scope="city_municipality",
        point_place="Columbia",
        point_geoid="2915670",
        point_geoid_level="place",
        point_method="focus_model",
    )
    regional = _article(2, link, author=None)
    ArticleEnrichment.objects.create(
        article=regional,
        scope="regional",
        geo_skip_reason="regional_uses_place_set",
    )
    uncodeable = _article(3, link)
    ArticleEnrichment.objects.create(
        article=uncodeable,
        scope="state",
        geo_skip_reason="no_codeable_geography",
    )
    _article(4, link)  # no enrichment record at all


def test_scope_filter(client, viewer, geo_corpus):
    assert _titles(client.get(URL, {"scope": "regional"})) == ["Story 2"]
    assert _titles(client.get(URL, {"scope": "city_municipality"})) == ["Story 1"]


def test_has_central_fips_filter(client, viewer, geo_corpus):
    assert _titles(client.get(URL, {"fips": "yes"})) == ["Story 1"]
    # "no" covers both a record without a claim and no record at all.
    assert _titles(client.get(URL, {"fips": "no"})) == [
        "Story 2",
        "Story 3",
        "Story 4",
    ]


def test_geo_skip_reason_filter(client, viewer, geo_corpus):
    response = client.get(URL, {"geo_skip": "regional_uses_place_set"})
    assert _titles(response) == ["Story 2"]


def test_geo_vocabularies_come_from_the_data(client, viewer, geo_corpus):
    content = client.get(URL).content.decode()
    for value in (
        "city_municipality",
        "regional",
        "regional_uses_place_set",
        "no_codeable_geography",
    ):
        assert value in content


def test_grid_shows_byline_presence_scope_and_central_place(client, viewer, geo_corpus):
    content = client.get(URL).content.decode()
    # The byline column carries the name itself, truncated, and says so
    # plainly when there is none.
    assert "Jane Reporter" in content
    assert ">none<" in content
    assert "no claim" in content
    assert "Columbia" in content
    assert "2915670" in content


def test_sort_defaults_to_newest_first(client, viewer, corpus):
    order = _order_of(client.get(URL), ("Story 1", "Story 3", "Story 2"))
    assert order.index("Story 2") == len(order) - 1


def test_sort_by_publication(client, viewer, corpus):
    # Publications sort by name; a hostname is not a sortable identity.
    content = _results(client, {"sort": "publication"})
    assert content.index("Herald") < content.index("Tribune")
    body = _results(client, {"sort": "publication", "dir": "desc"})
    assert body.index("Tribune") < body.index("Herald")


def test_sort_by_date_ascending(client, viewer, corpus):
    content = client.get(URL, {"sort": "date", "dir": "asc"}).content.decode()
    assert content.index("Story 2") < content.index("Story 1")


def test_unknown_sort_falls_back_to_date(client, viewer, corpus):
    from explorer.views import _sort_state

    assert _sort_state({"sort": "content; DROP TABLE"}) == ("date", "desc")
    assert _sort_state({"sort": "publication"}) == ("publication", "asc")
    assert _sort_state({"sort": "date", "dir": "sideways"}) == ("date", "desc")
    assert client.get(URL, {"sort": "nope", "dir": "nope"}).status_code == 200


def test_sort_links_carry_the_active_filters(client, viewer, corpus):
    content = client.get(URL, {"status": "labeled"}).content.decode()
    assert "status=labeled&amp;sort=publication" in content


def test_filter_form_carries_the_active_sort(client, viewer, corpus):
    content = client.get(URL, {"sort": "publication", "dir": "desc"}).content.decode()
    assert 'name="sort" value="publication"' in content
    assert 'name="dir" value="desc"' in content


def _results(client, params=None):
    """The rows, without the page around them.

    The publisher facet lists every newsroom in the filter bar, above the
    grid and alphabetically, so searching the whole page for a name finds
    the filter rather than the row. Asking for the htmx fragment returns
    the results region alone, which is what these assertions are about.
    """
    response = client.get(URL, params or {}, HTTP_HX_REQUEST="true")
    return response.content.decode()


def _order_of(response, needles):
    content = response.content.decode()
    return sorted((n for n in needles if n in content), key=lambda n: content.index(n))


def test_empty_results_say_so(client, viewer, corpus):
    """An empty django Page is falsy, so `{% if page %}` hid the empty
    row exactly when it was wanted."""
    response = client.get(URL, {"publisher": "nowhere.example"})
    assert "No articles match these filters." in response.content.decode()


@pytest.mark.parametrize("path", [URL, "/explorer/articles/a1/", "/review/queue/", "/"])
def test_no_template_syntax_reaches_the_page(client, viewer, corpus, path):
    """`{# #}` cannot span lines: a multi-line one is not a comment, and
    its text renders into the page. This caught it in a column header."""
    content = client.get(path).content.decode()
    for marker in ("{#", "#}", "{%", "%}", "{{", "}}"):
        assert marker not in content, f"{marker} leaked into {path}"


# --- the publisher facet -----------------------------------------------------


def test_the_publisher_facet_is_the_same_tree_the_builder_offers(
    client, viewer, corpus
):
    """State, then county, then the newsrooms. The search box that stood
    here asked a reader to know a name before they could narrow by one."""
    page = client.get(URL).content.decode()
    assert 'id="publisher-facet"' in page
    assert 'name="source"' in page
    assert "js/facet-tree.js" in page
    # Inside the block, or Django discards it and the facet never wires up.
    assert page.index("facet-tree.js") < page.index("</body>")
    # The box that takes a whole county, and no second implementation.
    assert 'class="branch"' in page
    assert "indeterminate" not in page
    # The control it replaced is gone from the page.
    assert 'name="publisher"' not in page


def test_choosing_publishers_narrows_the_grid(client, viewer, corpus):
    """Ids, not names: the tree already knows which record it drew."""
    from explorer.models import Article

    first = (
        Article.objects.select_related("candidate_link__source")
        .exclude(candidate_link__source__isnull=True)
        .first()
    )
    source_id = first.candidate_link.source_id

    everything = _results(client)
    narrowed = _results(client, {"source": source_id})
    assert first.title in narrowed
    assert narrowed.count("<tr") <= everything.count("<tr")

    # An id nobody has returns nothing rather than everything -- a filter
    # that fails open is worse than one that fails.
    assert "No articles match these filters." in _results(
        client, {"source": "no-such-source"}
    )


def test_several_publishers_are_one_filter(client, viewer, corpus):
    """Eleven publishers is one narrowing, not eleven chips, and taking a
    different filter off must not discard the other ten."""
    from django.http import QueryDict

    from explorer.templatetags.datadesk import active_filters

    params = QueryDict("source=1&source=2&source=3&status=enriched")
    chips = {chip["label"]: chip for chip in active_filters(params)}
    assert len(chips) == 2
    source_chip = next(c for c in chips.values() if c["key"] == "source")
    assert source_chip["value"] == "3 selected"

    # Removing the status filter keeps all three publishers.
    status_chip = next(c for c in chips.values() if c["key"] == "status")
    assert status_chip["without"].count("source=") == 3


def test_an_export_takes_every_chosen_publisher(client, viewer, corpus):
    """`.items()` on a QueryDict hands over the last value of a key. An
    export that quietly covered more than the grid it started from is the
    worst way for a filter to fail, because the file leaves the building."""
    from explorer.views import _many

    # The shape the export form posts back, and the shape a saved
    # definition stores.
    assert _many({"source": ["1", "2", "3"]}, "source") == ["1", "2", "3"]
    assert _many({"source": "1,2,3"}, "source") == ["1", "2", "3"]
    assert _many({"source": ""}, "source") == []

    page = client.get("/review/export/?source=1&source=2").content.decode()
    assert page.count('name="f_source"') == 2, "the export form dropped one"
