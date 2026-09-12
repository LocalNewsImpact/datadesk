"""What happens when a reviewer presses Submit.

Every part of this was unit-tested and the path itself was not, so a
submission that applied NOTHING reached production: it returned 302,
wrote neither a `ReviewDecision` nor an `article_places_manual` row, left
the article in the queue, and rendered a page identical to one where the
decision had worked. Reported as "it leaves decided records still in the
queue with no indication of a decision".

`submit` counts `incomplete`, `refused` and `unreachable` separately --
they are different things to be told -- and the page rendered none of
them.
"""

import datetime as dt

import pytest
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

QUEUE = reverse("review:geography")


@pytest.fixture
def reviewer(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    user.is_superuser = user.is_staff = True
    user.save()
    return user


@pytest.fixture
def unplaced(crawler_schema):
    """One article in exactly the state the queue selects on."""
    from explorer.models import (
        Article,
        ArticleEnrichment,
        CandidateLink,
        Dataset,
        DatasetSource,
        Source,
    )

    dataset = Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    source = Source.objects.create(
        id="s1",
        host="unterrified.example",
        host_norm="unterrified.example",
        canonical_name="The Unterrified Democrat",
        county="Osage",
        meta={"state": "MO"},
    )
    DatasetSource.objects.create(id="ds1", dataset=dataset, source=source)
    link = CandidateLink.objects.create(
        id="c1", source=source, url="https://unterrified.example/1"
    )
    article = Article.objects.create(
        id="a1",
        status="enrichment_skipped",
        candidate_link=link,
        dataset_id=dataset.id,
        title="A story nobody placed",
        publish_date=timezone.make_aware(dt.datetime(2026, 3, 10, 12)),
    )
    ArticleEnrichment.objects.create(
        article=article, scope="local", skip_reason="paywall_stub", cost_usd="0.00"
    )
    return article


def _rows(client):
    page = client.get(QUEUE + "?days=all")
    return page, page.content.decode()


def test_the_article_is_in_the_queue_to_begin_with(client, reviewer, unplaced):
    """The baseline every assertion below is measured against."""
    client.force_login(reviewer)
    _page, body = _rows(client)
    assert "A story nobody placed" in body


def test_a_place_is_recorded_and_the_article_leaves_the_queue(
    client, reviewer, unplaced
):
    from explorer.models import ArticlePlaceManual
    from review.models import ReviewDecision

    client.force_login(reviewer)
    client.post(
        QUEUE + "?days=all",
        {"d-a1": "set_place", "v-a1-set_place": "Linn, MO"},
    )

    decision = ReviewDecision.objects.get(queue="geography")
    assert (decision.subject_id, decision.verb) == ("a1", "set_place")
    row = ArticlePlaceManual.objects.get(article_id="a1")
    assert (row.geoid, row.is_point) == ("2943238", True)

    # And it is gone from the queue, which is the visible half of the
    # decision having been taken.
    _page, body = _rows(client)
    assert "A story nobody placed" not in body


def test_a_verb_with_no_place_says_so_instead_of_silently_doing_nothing(
    client, reviewer, unplaced
):
    """THE REPORTED FAULT. The verb requires a value; without one the
    decision is incomplete and nothing is written -- which is correct --
    and the page said nothing at all, so the row sat there looking
    undecided for no stated reason."""
    from explorer.models import ArticlePlaceManual
    from review.models import ReviewDecision

    client.force_login(reviewer)
    response = client.post(
        QUEUE + "?days=all",
        {"d-a1": "set_place", "v-a1-set_place": ""},
        follow=True,
    )

    assert not ReviewDecision.objects.exists()
    assert not ArticlePlaceManual.objects.exists()
    # Whitespace-normalised: the template wraps, so a phrase that spans
    # a line break is not in the raw body as written.
    body = " ".join(response.content.decode().split())
    assert "not recorded: a verb was chosen with no place given" in body
    # Still in the queue, as it should be -- but now it says why.
    assert "A story nobody placed" in body


def test_a_name_the_gazetteer_does_not_have_is_reported_not_a_500(
    client, reviewer, unplaced
):
    """ "Fatima, MO" is a real community in Osage County, unincorporated
    and so in no gazetteer. It used to raise out of `apply` and return an
    error page, losing every other decision on it."""
    from review.models import ReviewDecision

    client.force_login(reviewer)
    response = client.post(
        QUEUE + "?days=all",
        {"d-a1": "also_mentions", "v-a1-also_mentions": "Fatima, MO"},
        follow=True,
    )

    assert response.status_code == 200
    assert not ReviewDecision.objects.exists()
    body = response.content.decode()
    assert "not recorded" in body
    assert "Fatima" in body


def test_one_bad_row_does_not_lose_the_others(client, reviewer, unplaced):
    """A page is a session of decisions. One value the queue cannot carry
    out must not take the rest with it."""
    from explorer.models import Article, ArticleEnrichment
    from review.models import ReviewDecision

    second = Article.objects.create(
        id="a2",
        status="enrichment_skipped",
        candidate_link_id="c1",
        dataset_id="d1",
        title="Another story",
        publish_date=timezone.make_aware(dt.datetime(2026, 3, 11, 12)),
    )
    ArticleEnrichment.objects.create(
        article=second, scope="local", skip_reason="paywall_stub", cost_usd="0.00"
    )

    client.force_login(reviewer)
    client.post(
        QUEUE + "?days=all",
        {
            "d-a1": "also_mentions",
            "v-a1-also_mentions": "Fatima, MO",
            "d-a2": "also_mentions",
            "v-a2-also_mentions": "Linn, MO",
        },
    )

    assert [d.subject_id for d in ReviewDecision.objects.all()] == ["a2"]


def test_saying_there_is_no_place_is_a_decision(client, reviewer, unplaced):
    """It writes no geography and must still leave the queue -- that is
    what stops it asking again."""
    from explorer.models import ArticlePlaceManual
    from review.models import ReviewDecision

    client.force_login(reviewer)
    client.post(QUEUE + "?days=all", {"d-a1": "nothing_to_add"})

    assert ReviewDecision.objects.get(queue="geography").verb == "nothing_to_add"
    assert not ArticlePlaceManual.objects.exists()
    _page, body = _rows(client)
    assert "A story nobody placed" not in body


def test_submitting_an_empty_page_says_nothing_was_submitted(
    client, reviewer, unplaced
):
    """Distinct from every other outcome: no row carried a verb, so there
    was nothing to apply and nothing went wrong."""
    client.force_login(reviewer)
    response = client.post(QUEUE + "?days=all", {}, follow=True)
    assert "Nothing was submitted" in response.content.decode()


def test_several_mentions_are_one_decision_and_several_rows(client, reviewer, unplaced):
    from explorer.models import ArticlePlaceManual
    from review.models import ReviewDecision

    client.force_login(reviewer)
    client.post(
        QUEUE + "?days=all",
        {"d-a1": "also_mentions", "v-a1-also_mentions": "Linn, MO; Westphalia, MO"},
    )

    assert ReviewDecision.objects.filter(queue="geography").count() == 1
    assert sorted(ArticlePlaceManual.objects.values_list("geoid", flat=True)) == [
        "2943238",
        "2978910",
    ]
