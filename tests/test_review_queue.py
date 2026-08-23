"""The extraction review queue (SCOPE.md §2.3), read-only.

The fixtures use the skip_reason vocabulary production actually holds:

    removed_in_march_review              3695  human removals — NOT queued
    paywall_stub_exported_unenriched      968  the bulk March update
    scope_recorded_not_excluded            69  scope kept, recorded
    paywall_stub                           13  what the pipeline writes now

Both paywall spellings mean the same thing and both belong in the queue.
`scope_excluded_<category>` is what the pipeline writes going forward and
is matched by prefix. `removed_in_march_review` is a decision a person
already made and must never appear.
"""

from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
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
    """A reviewer, despite the name -- the queue is for the people who
    work it.

    This fixture signed in a viewer, because the three-group model let any
    assigned role reach the queue. ROADMAP item 6 opens by calling that
    wrong, and item 1 gives the vocabulary to say so: the queue asks for
    `write`, which a reviewer holds and a viewer does not.
    """
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="reviewer")
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

    # The other spelling, from the bulk March update. Same meaning.
    bulk = _article(
        "bulk",
        cl1,
        "Paywalled: county budget",
        "enrichment_skipped",
        text="x" * 300,
        author="Sam Reporter",
    )
    ArticleEnrichment.objects.create(
        article=bulk, skip_reason="paywall_stub_exported_unenriched"
    )

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

    scoped = _article(
        "scoped",
        cl2,
        "Local firm wins a contract in Berlin",
        "enrichment_skipped",
        text="z" * 3000,
        author="Pat Local",
    )
    ArticleEnrichment.objects.create(
        article=scoped, skip_reason="scope_recorded_not_excluded"
    )

    # What the pipeline writes for a scope exclusion from now on; matched
    # by prefix because no such row exists yet in production.
    future = _article(
        "future",
        cl2,
        "Council debates a treaty resolution",
        "enrichment_skipped",
        text="q" * 700,
        author="Alex Local",
    )
    ArticleEnrichment.objects.create(
        article=future, skip_reason="scope_excluded_international"
    )

    # A decision a person already made. Never a queue item.
    removed = _article(
        "removed",
        cl1,
        "Removed from the March sheet",
        "enrichment_skipped",
        text="m" * 2500,
        author="Chris Reporter",
    )
    ArticleEnrichment.objects.create(
        article=removed, skip_reason="removed_in_march_review"
    )

    # Not flagged: an ordinary enriched article must never appear.
    _article("fine", cl1, "Ordinary story", "enriched", text="w" * 4000)

    # enrichment_skipped for a reason the queue does not claim.
    other = _article("other", cl1, "Skipped for another reason", "enrichment_skipped")
    ArticleEnrichment.objects.create(article=other, skip_reason="some_other_reason")


def _titles(response):
    content = response.content.decode()
    return [
        title
        for title in (
            "Subscribers only: council votes",
            "Paywalled: county budget",
            "Photo gallery",
            "County budget hearing draws a crowd",
            "Local firm wins a contract in Berlin",
            "Council debates a treaty resolution",
            "Removed from the March sheet",
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


def test_enrichment_skipped_needs_a_reason_the_queue_claims(client, viewer, flagged):
    """enrichment_skipped alone is not a queue item: the skip reason has
    to be one of the values the queue is for."""
    assert "Skipped for another reason" not in _titles(client.get(URL))


def test_case_filter_narrows_to_one_case(client, viewer, flagged):
    assert set(_titles(client.get(URL, {"case": "paywall_stub"}))) == {
        "Subscribers only: council votes",
        "Paywalled: county budget",
    }
    assert set(_titles(client.get(URL, {"case": "minimal_capture"}))) == {
        "Photo gallery",
        "County budget hearing draws a crowd",
    }
    assert set(_titles(client.get(URL, {"case": "scope_mislabel"}))) == {
        "Local firm wins a contract in Berlin",
        "Council debates a treaty resolution",
    }


def test_unknown_case_is_ignored_rather_than_erroring(client, viewer, flagged):
    response = client.get(URL, {"case": "invented"})
    assert response.status_code == 200
    assert len(_titles(response)) == 6


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

    counts = {band["key"]: band["count"] for band in band_facets({}, viewer)}
    assert counts == {"empty": 1, "stub": 2, "short": 1, "medium": 0, "long": 2}


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

    counts = {
        band["key"]: band["count"] for band in band_facets({"band": "long"}, viewer)
    }
    assert counts["empty"] == 1
    assert counts["long"] == 2


def test_bands_combine_with_a_case(client, viewer, flagged):
    titles = _titles(client.get(URL, {"case": "minimal_capture", "band": "long"}))
    assert titles == ["County budget hearing draws a crowd"]


def test_empty_band_selects_articles_with_no_text(client, viewer, flagged):
    assert _titles(client.get(URL, {"band": "empty"})) == ["Photo gallery"]


def test_case_facet_counts(client, viewer, flagged):
    from review.queue import case_facets

    counts = {case["key"]: case["count"] for case in case_facets({}, viewer)}
    assert counts == {
        "paywall_stub": 2,
        "minimal_capture": 2,
        "scope_mislabel": 2,
    }


# --- other filters ----------------------------------------------------------


def test_dataset_filter_follows_membership(client, viewer, flagged):
    titles = _titles(client.get(URL, {"dataset": "missouri"}))
    assert set(titles) == {
        "Subscribers only: council votes",
        "Paywalled: county budget",
        "Photo gallery",
    }


def test_publisher_filter(client, viewer, flagged):
    titles = _titles(client.get(URL, {"publisher": "herald"}))
    assert set(titles) == {
        "County budget hearing draws a crowd",
        "Local firm wins a contract in Berlin",
        "Council debates a treaty resolution",
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
    """The filter offers the queue's own reasons, so every value returns
    rows — and never offers one the queue does not hold."""
    content = client.get(URL).content.decode()
    assert "paywall_stub_exported_unenriched" in content
    assert "scope_recorded_not_excluded" in content
    assert "some_other_reason" not in content
    assert "removed_in_march_review" not in content


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


def test_wire_status_is_mapped_for_humans(client, viewer, flagged):
    """'complete' and 'local' both mean the check passed; the raw string
    never reaches the screen."""
    content = client.get(URL).content.decode()
    assert ">Local<" in content
    assert 'title="complete"' in content


def test_a_syndicated_queue_row_names_the_service(client, viewer, flagged):
    from explorer.models import Article

    article = Article.objects.get(id="long")
    article.wire_check_status = "wire"
    article.wire = ["Associated Press"]
    article.save()
    content = client.get(URL).content.decode()
    assert "Wire: Associated Press" in content


def test_an_unfinished_check_is_not_a_local_story(client, viewer, flagged):
    from explorer.models import Article

    article = Article.objects.get(id="long")
    article.wire_check_status = "error"
    article.save()
    assert "Check incomplete" in client.get(URL).content.decode()


def test_statuses_are_labelled(client, viewer, flagged):
    content = client.get(URL).content.decode()
    assert "Not an article" in content
    assert "Exported unenriched" in content
    assert 'title="not_article"' in content


def test_an_unknown_wire_value_reads_as_unfinished_not_local(client, viewer, flagged):
    """Only 'complete', 'local' and 'wire' mean the check concluded. A
    value this code does not know has not concluded, and must not be
    presented as a local story."""
    from explorer.templatetags.datadesk import wire_label, wire_tone

    assert wire_label("some_future_state") == "Check incomplete"
    assert wire_tone("some_future_state") == "incomplete"
    assert wire_label("local") == "Local"
    assert wire_tone("complete") == "local"
    assert wire_tone("wire") == "wire"


# --- the skip_reason vocabulary, as production holds it ---------------------


def test_human_removals_are_never_in_the_queue(client, viewer, flagged):
    """removed_in_march_review is not an extraction finding: those 3,695
    rows are membership removals a person already decided. Queuing them
    would ask an operator to re-review decisions they have taken."""
    assert "Removed from the March sheet" not in _titles(client.get(URL))
    for case in ("paywall_stub", "minimal_capture", "scope_mislabel"):
        assert "Removed from the March sheet" not in _titles(
            client.get(URL, {"case": case})
        )
    # Including via the length band that would otherwise catch it.
    assert "Removed from the March sheet" not in _titles(
        client.get(URL, {"band": "long"})
    )


def test_the_exclusion_survives_a_status_the_queue_does_not_expect(
    client, viewer, flagged
):
    """The exclusion is applied to the whole query, not per case, so a
    human removal cannot reappear under a status nobody considered."""
    from explorer.models import Article

    article = Article.objects.get(id="removed")
    article.status = "not_article"
    article.save()
    assert "Removed from the March sheet" not in _titles(client.get(URL))


def test_both_paywall_spellings_are_queued(client, viewer, flagged):
    """The bulk March update and the live pipeline write different
    strings for the same finding."""
    titles = _titles(client.get(URL, {"case": "paywall_stub"}))
    assert "Subscribers only: council votes" in titles  # paywall_stub
    assert "Paywalled: county budget" in titles  # ..._exported_unenriched


def test_future_scope_exclusions_match_by_prefix(client, viewer, flagged):
    """The pipeline writes scope_excluded_<category> going forward; none
    exist in production yet, so the queue cannot enumerate them."""
    assert "Council debates a treaty resolution" in _titles(
        client.get(URL, {"case": "scope_mislabel"})
    )


def test_the_selection_is_exact_values_not_substrings(client, viewer, flagged):
    """A substring match on "stub" or "scope" would sweep in whatever the
    pipeline adds next. Nothing outside the known vocabulary is queued."""
    from explorer.models import Article, ArticleEnrichment

    link = Article.objects.get(id="stub").candidate_link
    for reason in (
        "scope_review_pending",
        "stub_article_merged",
        "paywalled_by_hand",
    ):
        article = _article(reason, link, f"Title {reason}", "enrichment_skipped")
        ArticleEnrichment.objects.create(article=article, skip_reason=reason)

    content = client.get(URL).content.decode()
    for reason in ("scope_review_pending", "stub_article_merged", "paywalled_by_hand"):
        assert f"Title {reason}" not in content


def test_the_queues_vocabulary_matches_production():
    """The values themselves, pinned. Queried from production 2026-08-22;
    a change to the pipeline's vocabulary must update this list too."""
    from review import queue as q

    assert q.PAYWALL_STUB_SKIP_REASONS == (
        "paywall_stub",
        "paywall_stub_exported_unenriched",
    )
    assert q.SCOPE_SKIP_REASONS == ("scope_recorded_not_excluded",)
    assert q.SCOPE_SKIP_REASON_PREFIX == "scope_excluded_"
    assert q.HUMAN_REMOVAL_SKIP_REASON == "removed_in_march_review"
