"""The extraction review queue (SCOPE.md §2.3), read-only.

The fixtures reproduce the three March 2026 cases: a paywall stub whose
text is a teaser but whose CIN label and byline are intact, a minimal
capture flagged not_article, a full-length article wrongly flagged
not_article (38 of 206 in March carried more than 2000 characters), and a
legacy scope mislabel.
"""

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

URL = "/review/queue/"


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="viewer"))
    client.force_login(user)
    return user


def _article(article_id, link, title, status, text="", **overrides):
    fields = {
        "id": article_id,
        "candidate_link": link,
        "url": f"https://example.org/{article_id}",
        "title": title,
        "status": status,
        "wire_check_status": "complete",
        "created_at": datetime(2026, 3, 1, tzinfo=UTC),
        "publish_date": datetime(2026, 3, 1, tzinfo=UTC),
        "content": text,
        "primary_label": "government",
        "primary_label_confidence": 0.9,
    }
    fields.update(overrides)
    return Article.objects.create(**fields)


@pytest.fixture
def flagged(crawler_schema):
    dataset = Dataset.objects.create(id="d1", slug="missouri", label="Missouri")
    tribune = Source.objects.create(
        id="s1", host="tribune.example", host_norm="tribune.example"
    )
    herald = Source.objects.create(
        id="s2", host="herald.example", host_norm="herald.example"
    )
    DatasetSource.objects.create(id="ds1", dataset=dataset, source=tribune)
    cl1 = CandidateLink.objects.create(id="cl1", url="https://t/", source=tribune)
    cl2 = CandidateLink.objects.create(id="cl2", url="https://h/", source=herald)

    stub = _article(
        "stub",
        cl1,
        "Subscribers only: council votes",
        "enrichment_skipped",
        text="x" * 265,
        author="Jane Reporter",
    )
    ArticleEnrichment.objects.create(article=stub, skip_reason="paywall_stub")

    empty = _article("empty", cl1, "Photo gallery", "not_article", text="")
    ArticleEnrichment.objects.create(
        article=empty, content_gate_reason="no_body_text", is_news_content=False
    )

    # The March finding: a full-length story flagged not_article.
    _article(
        "long",
        cl2,
        "County budget hearing draws a crowd",
        "not_article",
        text="y" * 2400,
        author="Sam Byline",
    )

    _article(
        "scoped",
        cl2,
        "Local firm wins a contract in Berlin",
        "out_of_scope",
        text="z" * 3000,
        author="Pat Local",
    )

    # Not flagged: an ordinary enriched article must never appear.
    _article("fine", cl1, "Ordinary story", "enriched", text="w" * 4000)

    # enrichment_skipped for a reason that is not a paywall stub.
    other = _article("other", cl1, "Skipped for another reason", "enrichment_skipped")
    ArticleEnrichment.objects.create(article=other, skip_reason="duplicate_of_earlier")


def _titles(response):
    content = response.content.decode()
    return [
        title
        for title in (
            "Subscribers only: council votes",
            "Photo gallery",
            "County budget hearing draws a crowd",
            "Local firm wins a contract in Berlin",
            "Ordinary story",
            "Skipped for another reason",
        )
        if title in content
    ]


# --- access -----------------------------------------------------------------


def test_anonymous_is_sent_to_sign_in(client):
    response = client.get(URL)
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


def test_no_role_is_forbidden(client):
    client.force_login(User.objects.create_user("norole", email="n@example.org"))
    assert client.get(URL).status_code == 403


def test_a_viewer_may_read_the_queue(client, viewer, flagged):
    assert client.get(URL).status_code == 200


# --- row selection ----------------------------------------------------------


def test_queue_selects_the_three_flagged_cases_only(client, viewer, flagged):
    titles = _titles(client.get(URL))
    assert "Subscribers only: council votes" in titles
    assert "Photo gallery" in titles
    assert "County budget hearing draws a crowd" in titles
    assert "Local firm wins a contract in Berlin" in titles
    # An enriched article is not a review item.
    assert "Ordinary story" not in titles


def test_enrichment_skipped_needs_a_paywall_stub_reason(client, viewer, flagged):
    """enrichment_skipped alone is not a queue item; the skip reason has
    to read as a stub."""
    assert "Skipped for another reason" not in _titles(client.get(URL))


def test_case_filter_narrows_to_one_case(client, viewer, flagged):
    stubs = _titles(client.get(URL, {"case": "paywall_stub"}))
    assert stubs == ["Subscribers only: council votes"]

    minimal = _titles(client.get(URL, {"case": "minimal_capture"}))
    assert set(minimal) == {"Photo gallery", "County budget hearing draws a crowd"}

    mislabels = _titles(client.get(URL, {"case": "scope_mislabel"}))
    assert mislabels == ["Local firm wins a contract in Berlin"]


def test_unknown_case_is_ignored_rather_than_erroring(client, viewer, flagged):
    response = client.get(URL, {"case": "invented"})
    assert response.status_code == 200
    assert len(_titles(response)) == 4


# --- the length bands -------------------------------------------------------


def test_longest_captures_come_first(client, viewer, flagged):
    content = client.get(URL).content.decode()
    positions = [
        content.index("Local firm wins a contract in Berlin"),
        content.index("County budget hearing draws a crowd"),
        content.index("Subscribers only: council votes"),
        content.index("Photo gallery"),
    ]
    assert positions == sorted(positions)


def test_band_facet_counts_every_band(client, viewer, flagged):
    from review.queue import band_facets

    counts = {band["key"]: band["count"] for band in band_facets({})}
    assert counts == {"empty": 1, "stub": 1, "short": 0, "medium": 0, "long": 2}


def test_long_band_surfaces_the_wrongly_flagged_articles(client, viewer, flagged):
    """The March finding: 38 of 206 not_article rows carried more than
    2000 characters. The band is how an operator reaches them."""
    titles = _titles(client.get(URL, {"band": "long"}))
    assert set(titles) == {
        "County budget hearing draws a crowd",
        "Local firm wins a contract in Berlin",
    }


def test_band_facet_ignores_the_selected_band(client, viewer, flagged):
    """A facet that counted only the selection would restate the result
    count and tell the operator nothing."""
    from review.queue import band_facets

    counts = {band["key"]: band["count"] for band in band_facets({"band": "long"})}
    assert counts["empty"] == 1
    assert counts["long"] == 2


def test_bands_combine_with_a_case(client, viewer, flagged):
    titles = _titles(client.get(URL, {"case": "minimal_capture", "band": "long"}))
    assert titles == ["County budget hearing draws a crowd"]


def test_empty_band_selects_articles_with_no_text(client, viewer, flagged):
    assert _titles(client.get(URL, {"band": "empty"})) == ["Photo gallery"]


def test_case_facet_counts(client, viewer, flagged):
    from review.queue import case_facets

    counts = {case["key"]: case["count"] for case in case_facets({})}
    assert counts == {
        "paywall_stub": 1,
        "minimal_capture": 2,
        "scope_mislabel": 1,
    }


# --- other filters ----------------------------------------------------------


def test_dataset_filter_follows_membership(client, viewer, flagged):
    titles = _titles(client.get(URL, {"dataset": "missouri"}))
    assert set(titles) == {"Subscribers only: council votes", "Photo gallery"}


def test_publisher_filter(client, viewer, flagged):
    titles = _titles(client.get(URL, {"publisher": "herald"}))
    assert set(titles) == {
        "County budget hearing draws a crowd",
        "Local firm wins a contract in Berlin",
    }


def test_byline_filter(client, viewer, flagged):
    assert _titles(client.get(URL, {"byline": "no"})) == ["Photo gallery"]
    assert "Photo gallery" not in _titles(client.get(URL, {"byline": "yes"}))


def test_exact_skip_reason_filter(client, viewer, flagged):
    """The marker heuristic is a default, not a ceiling: the exact
    reasons in the data are selectable too."""
    titles = _titles(client.get(URL, {"skip": "paywall_stub"}))
    assert titles == ["Subscribers only: council votes"]


def test_skip_reason_vocabulary_comes_from_the_data(client, viewer, flagged):
    content = client.get(URL).content.decode()
    assert "paywall_stub" in content
    assert "duplicate_of_earlier" in content


# --- what the row shows -----------------------------------------------------


def test_rows_show_length_reason_label_and_byline(client, viewer, flagged):
    content = client.get(URL).content.decode()
    assert "265" in content  # captured text length
    assert "paywall_stub" in content  # the reason triage gave
    assert "no_body_text" in content  # the gate's reason
    assert "government" in content  # the CIN label
    assert "0.90" in content  # its confidence
    assert "Jane Reporter" in content  # the byline that survived
    assert "/explorer/articles/stub/" in content  # link to the detail view


# --- read-only, and what comes next ----------------------------------------


def test_the_queue_writes_nothing(client, viewer, flagged):
    """No disposition exists yet; the placeholder says so and its buttons
    are inert."""
    # The results fragment, so the layout's sign-out form is out of frame.
    content = client.get(URL, HTTP_HX_REQUEST="true").content.decode()
    assert "Dispositions arrive in Phase 2b" in content
    assert content.count("disabled") >= 3
    for disposition in ("Skip", "Export unenriched", "Send to enrichment"):
        assert disposition in content
    assert "<form" not in content
    assert "csrfmiddlewaretoken" not in content


def test_queue_module_exposes_no_write_path():
    import review.queue as queue_module

    for name in dir(queue_module):
        assert "update" not in name
        assert "save" not in name
        assert "delete" not in name


# --- degradation ------------------------------------------------------------


def test_degrades_without_the_crawler_database(client, viewer):
    response = client.get(URL)
    assert response.status_code == 200
    assert "not connected" in response.content.decode()


def test_htmx_request_gets_only_the_results_fragment(client, viewer, flagged):
    response = client.get(URL, HTTP_HX_REQUEST="true")
    content = response.content.decode()
    assert "Photo gallery" in content
    assert "<html" not in content
    assert "filter-bar" not in content


def test_empty_result_says_something_useful(client, viewer, flagged):
    response = client.get(URL, {"publisher": "nowhere.example"})
    assert "Nothing flagged under these filters" in response.content.decode()
