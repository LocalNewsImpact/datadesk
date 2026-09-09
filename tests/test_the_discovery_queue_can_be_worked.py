"""The discovery queue asks about a URL, before anything was fetched.

There is no body and no byline here, so the evidence is the address
itself: the queue is unusable unless the link opens. That is the one
piece of the UI the rest of this file exists to protect.

The strata are the other half. A doubt-ranked set finds errors and can
never say how many there are, because it is drawn from rows a signal
already suspects; a random sample says how many and finds almost none.
Both are kept, and each label records which drew it and with what
probability -- a rate measured over rows that were not equally likely to
be drawn is not an error rate.
"""

from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from explorer.models import CandidateLink, Source, UrlVerification
from review import discovery
from review.models import ReviewDecision

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def reviewer(db):
    user = User.objects.create_user("dr", email="dr@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    return user


def _link(crawler_schema, link_id, discovered="2026-03-10", status="not_article"):
    source = Source.objects.using("crawler").filter(id="s1").first()
    if source is None:
        source = Source.objects.using("crawler").create(
            id="s1", host="example.com", canonical_name="The Example"
        )
    return CandidateLink.objects.using("crawler").create(
        id=link_id,
        url=f"https://example.com/{link_id}",
        source=source,
        status=status,
        discovered_at=datetime.fromisoformat(f"{discovered}T12:00:00").replace(
            tzinfo=UTC
        ),
    )


def _verification(crawler_schema, link, margin, sniffer, status="not_article"):
    return UrlVerification.objects.using("crawler").create(
        id=f"v-{link.id}",
        candidate_link=link,
        url=link.url,
        storysniffer_result=sniffer,
        verification_confidence=margin,
        new_status=status,
    )


# ---------------------------------------------------------------- strata


@pytest.mark.parametrize(
    "margin,sniffer,stratum,expected",
    [
        # Both sides of the boundary are doubtful, not just the rejections.
        (5.0, True, discovery.DOUBTFUL, True),
        (-5.0, False, discovery.DOUBTFUL, True),
        (25.0, True, discovery.DOUBTFUL, True),
        (26.0, True, discovery.DOUBTFUL, False),
        # Overruled is a positive margin the pipeline rejected anyway --
        # the model did not make that call, an override did.
        (500.0, False, discovery.OVERRULED, True),
        # A positive margin the model itself acted on is not overruled.
        (500.0, True, discovery.OVERRULED, False),
        # Nor is a rejection the model agrees with.
        (-500.0, False, discovery.OVERRULED, False),
    ],
)
def test_a_stratum_selects_what_it_says(
    crawler_schema, margin, sniffer, stratum, expected
):
    link = _link(crawler_schema, f"l{abs(int(margin))}{sniffer}")
    _verification(crawler_schema, link, margin, sniffer)
    found = UrlVerification.objects.using("crawler").filter(
        discovery.predicate(stratum)
    )
    assert found.exists() is expected


def test_the_random_sample_is_not_restricted_to_leftovers(crawler_schema):
    """Its predicate is empty on purpose.

    Drawing it from the rows the doubt-ranked strata did not take would
    make it a sample of the unsuspicious, which cannot estimate the error
    rate of the whole.
    """
    assert discovery.predicate(discovery.SAMPLE) == discovery.Q()


def test_a_draw_is_stable_between_requests(crawler_schema):
    """A sample that reshuffles on every request is not a held-out set."""
    for n in range(12):
        link = _link(crawler_schema, f"stable{n}")
        _verification(crawler_schema, link, 500.0, False)
    qs = UrlVerification.objects.using("crawler").filter(
        discovery.predicate(discovery.OVERRULED)
    )
    first = [r.id for r in discovery.drawn(qs, discovery.OVERRULED)]
    second = [r.id for r in discovery.drawn(qs, discovery.OVERRULED)]
    assert first == second and first


def test_full_review_and_a_capped_draw_carry_different_weights():
    assert discovery.inclusion_probability(discovery.DOUBTFUL, 746) == 1.0
    assert discovery.inclusion_probability(discovery.SAMPLE, 800) == 0.5
    # Never above 1: a stratum smaller than its cap was reviewed whole.
    assert discovery.inclusion_probability(discovery.SAMPLE, 100) == 1.0


# ------------------------------------------------------------------- UI


def test_each_record_links_out_to_the_url(client, reviewer, crawler_schema):
    """The judgement cannot be made without opening the page.

    `rel="noopener"` alongside `target="_blank"`: without it the opened
    page gets a handle on this one through window.opener.
    """
    link = _link(crawler_schema, "opens")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    body = client.get(reverse("review:discovery")).content.decode()
    assert 'href="https://example.com/opens"' in body
    assert 'target="_blank"' in body
    assert "noopener" in body


def test_the_page_says_how_the_rows_were_drawn(client, reviewer, crawler_schema):
    """A reviewer reading a doubt-ranked list should know that is what it
    is, or they will read the error count as the error rate."""
    link = _link(crawler_schema, "drawn")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    body = client.get(reverse("review:discovery")).content.decode()
    assert "Doubtful" in body and "Random sample" in body


# ---------------------------------------------------------- the verbs


def test_calling_it_a_story_returns_the_url_to_the_pipeline(
    client, reviewer, crawler_schema
):
    link = _link(crawler_schema, "isastory", status="not_article")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    client.post(
        reverse("review:discovery"),
        {
            f"d-{link.id}": discovery.IT_IS_A_STORY,
            f"v-{link.id}": "story",
            f"stratum-{link.id}": discovery.DOUBTFUL,
            f"probability-{link.id}": "1.0",
        },
    )
    link.refresh_from_db()
    assert link.status == "discovered"


def test_not_a_story_leaves_the_crawler_alone(client, reviewer, crawler_schema):
    """The status already excludes it. The decision is recorded so the
    queue stops asking, and nothing is written to the crawler."""
    link = _link(crawler_schema, "notastory", status="not_article")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    client.post(
        reverse("review:discovery"),
        {
            f"d-{link.id}": discovery.NOT_A_STORY,
            f"v-{link.id}": "section_index",
            f"stratum-{link.id}": discovery.DOUBTFUL,
            f"probability-{link.id}": "1.0",
        },
    )
    link.refresh_from_db()
    assert link.status == "not_article"
    assert ReviewDecision.objects.filter(subject_type="candidate_link").exists()


def test_the_label_records_the_stratum_and_the_odds(client, reviewer, crawler_schema):
    """Without these the doubt-ranked labels and the random ones cannot be
    told apart afterwards, and the set cannot state a confidence level."""
    link = _link(crawler_schema, "labelled")
    _verification(crawler_schema, link, 500.0, False)
    client.force_login(reviewer)
    client.post(
        reverse("review:discovery"),
        {
            f"d-{link.id}": discovery.NOT_A_STORY,
            f"v-{link.id}": "video",
            f"stratum-{link.id}": discovery.OVERRULED,
            f"probability-{link.id}": "0.024",
        },
    )
    wrote = ReviewDecision.objects.get(subject_id=link.id).wrote
    assert wrote["stratum"] == discovery.OVERRULED
    assert wrote["inclusion_probability"] == pytest.approx(0.024)
    assert wrote["what_it_is"] == "video"
    # The model's own numbers travel with the label, so a retrain does not
    # have to join back to a table that may have been re-verified since.
    assert wrote["margin"] == pytest.approx(500.0)
    assert wrote["storysniffer_result"] is False


def test_the_qualifier_keeps_a_profile_page_apart_from_a_story():
    """Extraction has produced article rows for /profile/ pages titled
    with the paper's own name. A vocabulary that cannot say "tag or author
    page" teaches a model that those are stories."""
    values = dict(discovery.WHAT_IT_IS)
    assert "tag_or_author" in values and "section_index" in values
    assert values["story"] == "Story"
