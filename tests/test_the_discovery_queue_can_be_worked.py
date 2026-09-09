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
            f"v-{link.id}-{discovery.IT_IS_A_STORY}": "news",
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
            f"v-{link.id}-{discovery.NOT_A_STORY}": "section_index",
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
            f"v-{link.id}-{discovery.NOT_A_STORY}": "video",
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
    values = discovery.what_it_is_labels()
    assert "tag_or_author" in values and "section_index" in values


def test_each_verb_asks_its_own_question():
    """ "What kind of story" and "what is it instead" have no answers in
    common. One shared list offered `homepage` as a kind of story."""
    story = {c["value"] for c in discovery.STORY_KINDS}
    not_story = {c["value"] for c in discovery.NOT_STORY_KINDS}
    # `other` is the one honest overlap: both questions can be unanswerable.
    assert story & not_story == {"other"}
    for wrong in ("homepage", "section_index", "video", "tag_or_author"):
        assert wrong not in story, f"{wrong} is offered as a kind of story"
    for wrong in ("news", "opinion", "obituary", "weather"):
        assert wrong not in not_story, f"{wrong} is offered as a non-story"


def test_the_story_kinds_are_the_extraction_queue_s_words():
    """The same judgement should not grow a second spelling one queue
    along."""
    from review.dispositions import CONTENT_TYPES

    known = {c["value"] for c in CONTENT_TYPES}
    for choice in discovery.STORY_KINDS:
        if choice["value"] == "other":
            continue
        assert choice["value"] in known, choice["value"]


def test_every_qualifier_choice_is_a_dict_the_template_can_render():
    """`review/_verbs.html` renders `choice.value` and `choice.label`.
    Given 2-tuples, Django resolves neither -- attribute, then key, then
    numeric index, and "value" is none of them -- so the list rendered
    with every option blank and the queue could not be answered."""
    for choice in discovery.STORY_KINDS + discovery.NOT_STORY_KINDS:
        assert isinstance(choice, dict), choice
        assert choice["value"] and choice["label"]


def test_the_page_offers_each_verb_its_own_list(client, reviewer, crawler_schema):
    link = _link(crawler_schema, "options")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    body = client.get(reverse("review:discovery")).content.decode()
    assert 'value="section_index"' in body, "the dropdown rendered empty"
    assert 'value="obituary"' in body, "the story list is missing"
    # One box per verb, each named for the verb it belongs to, so the two
    # cannot overwrite one another when the row is posted.
    assert f'name="v-{link.id}-{discovery.IT_IS_A_STORY}"' in body
    assert f'name="v-{link.id}-{discovery.NOT_A_STORY}"' in body


def test_the_page_loads_the_session_script(client, reviewer, crawler_schema):
    """Without it the verbs record nothing and Submit stays disabled with
    nothing able to enable it: a queue that can be read and not worked."""
    link = _link(crawler_schema, "script")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    body = client.get(reverse("review:discovery")).content.decode()
    assert "js/review-queue.js" in body
    # The three things that script looks for by name.
    assert 'id="queue-form"' in body
    assert 'id="q-submit"' in body
    assert 'class="prop"' in body or 'prop"' in body


def test_a_margin_is_shown_as_a_percentile_not_a_probability(
    client, reviewer, crawler_schema
):
    """The margin runs to six figures and means nothing to a reader.
    Nothing here is calibrated, so the page gives the percentile rather
    than a probability it cannot support."""
    # Inside the doubtful band, which is the stratum the page opens on.
    link = _link(crawler_schema, "ranked")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    body = client.get(reverse("review:discovery")).content.decode()
    assert "percentile" in body
    # The raw score belongs in the tooltip, not the column.
    assert "margin 5.0" not in body.split("title=")[0]


def test_the_percentile_places_a_margin_in_the_cohort():
    cuts = [float(n) for n in range(0, 101)]  # a flat 0..100 cohort
    assert discovery.percentile(-50.0, cuts) == 0
    assert discovery.percentile(50.0, cuts) == 50
    assert discovery.percentile(10_000.0, cuts) == 100
    assert discovery.percentile(None, cuts) is None
    assert discovery.percentile(5.0, []) is None


def test_the_value_posted_is_the_one_belonging_to_the_chosen_verb():
    """A row carries a box per verb. Reading `v-<id>` alone got whichever
    the browser happened to send last, which on this queue means the
    answer to the question the reviewer did not answer."""
    from review import submit

    posted = submit.posted(
        {
            "d-abc": discovery.NOT_A_STORY,
            f"v-abc-{discovery.IT_IS_A_STORY}": "news",
            f"v-abc-{discovery.NOT_A_STORY}": "homepage",
        }
    )
    assert posted["abc"] == (discovery.NOT_A_STORY, "homepage")


def test_a_queue_posting_the_bare_name_still_works():
    """Every other queue posts `v-<id>`, and this must not break them."""
    from review import submit

    assert submit.posted({"d-abc": "reject", "v-abc": "opinion"})["abc"] == (
        "reject",
        "opinion",
    )
