"""A facet chip counts what the list would show, and no more.

Filtering the extraction queue to one month of one dataset showed bands
adding to 1,218 against a shorter list. The dates and the dataset were
reaching the counts; two other narrowings were not.

`queued` drops questions somebody has already answered, and on the
landing view narrows to rows there is recorded reason to doubt. The band
and case counts were computed straight off `_apply_common` and did
neither, so every chip promised rows the list would not show -- decided
ones always, and on a bare queue the entire flagged backlog.
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.http import QueryDict
from django.utils import timezone

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from review import queue as review_queue
from review.models import ReviewDecision

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def reviewer():
    user = User.objects.create_user("rev", email="rev@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="reviewer")
    return user


@pytest.fixture
def flagged(crawler_schema):
    """Three flagged articles in one dataset, all long enough to be doubted."""
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    made = []
    for n in range(3):
        link = CandidateLink.objects.create(
            id=f"cl{n}",
            source_id=source.id,
            dataset_id=dataset.id,
            url=f"https://a.example/{n}",
        )
        made.append(
            Article.objects.create(
                id=f"a{n}",
                candidate_link=link,
                title=f"story {n}",
                status="not_article",
                wire_check_status="complete",
                content="A captured body worth reading." * 90,
                text="A captured body worth reading." * 90,
                author="Ellen Reporter",
                publish_date=timezone.now() - timedelta(days=2),
                created_at=timezone.now() - timedelta(days=2),
                enrichment_attempts=0,
            )
        )
    return made


def _bands(params, user):
    return {b["label"]: b["count"] for b in review_queue.band_facets(params, user)}


def test_the_bands_add_up_to_the_list(reviewer, flagged):
    params = QueryDict("dataset=mo")
    assert (
        sum(_bands(params, reviewer).values())
        == review_queue.queued(params, reviewer).count()
    )


def test_a_decided_article_leaves_the_counts_with_the_list(reviewer, flagged):
    """The failure that was reported: chips counting decided rows."""
    params = QueryDict("dataset=mo")
    before = review_queue.queued(params, reviewer).count()

    ReviewDecision.objects.create(
        queue="extraction",
        subject_type="article",
        subject_id="a0",
        field="",
        question=(
            review_queue.question_for("not_article", "extraction")
            if hasattr(review_queue, "question_for")
            else "is_it_an_article"
        ),
        claim="not_article",
        stage="extraction",
        verb="accept",
        before="not_article",
        after="not_article",
        decided_by=reviewer,
    )

    after = review_queue.queued(params, reviewer).count()
    assert after == before - 1, "the decision did not leave the list"
    assert sum(_bands(params, reviewer).values()) == after


def test_including_decided_puts_it_back_in_both(reviewer, flagged):
    params = QueryDict("dataset=mo&state=all")
    assert (
        sum(_bands(params, reviewer).values())
        == review_queue.queued(params, reviewer).count()
    )


def test_a_band_chip_promises_what_clicking_it_shows(reviewer, flagged):
    """The invariant, and it is not "the chips add up to the list".

    On the landing view the list narrows to what there is recorded
    reason to doubt, and clicking any chip switches that off -- so the
    chips can legitimately total more than the list beneath them. What
    must hold is that each chip's number is the length of the list it
    produces.
    """
    params = QueryDict("")
    for band in review_queue.band_facets(params, reviewer):
        chosen = QueryDict(f"band={band['key']}")
        assert (
            band["count"] == review_queue.queued(chosen, reviewer).count()
        ), f"the {band['key']} chip promises {band['count']} rows"


def test_the_empty_band_still_advertises_what_the_landing_view_hides(
    reviewer, flagged, crawler_schema
):
    """An empty capture is exactly what the landing view narrows away, and
    exactly what its chip exists to surface. Counting the chip the same
    way as the list made it read 0, so nobody would ever click it."""
    source = Source.objects.get(id="s1")
    link = CandidateLink.objects.create(
        id="cl-empty",
        source_id=source.id,
        dataset_id="d1",
        url="https://a.example/empty",
    )
    Article.objects.create(
        id="a-empty",
        candidate_link=link,
        title="nothing came back",
        status="not_article",
        wire_check_status="complete",
        content="",
        text="",
        publish_date=timezone.now() - timedelta(days=2),
        created_at=timezone.now() - timedelta(days=2),
        enrichment_attempts=0,
    )

    landing = review_queue.queued(QueryDict(""), reviewer)
    assert "a-empty" not in {a.id for a in landing}, "the landing view showed it"

    empty_chip = next(
        b
        for b in review_queue.band_facets(QueryDict(""), reviewer)
        if b["key"] == "empty"
    )
    assert empty_chip["count"] == 1, "the chip does not advertise it"
    assert "a-empty" in {
        a.id for a in review_queue.queued(QueryDict("band=empty"), reviewer)
    }


def test_case_counts_match_their_own_selection(reviewer, flagged):
    """Each case chip counts its own rows, so selecting one must produce a
    list of exactly that length."""
    params = QueryDict("dataset=mo")
    for case in review_queue.case_facets(params, reviewer):
        chosen = QueryDict(f"dataset=mo&case={case['key']}")
        assert (
            case["count"] == review_queue.queued(chosen, reviewer).count()
        ), f"the {case['key']} chip promises {case['count']} rows"
