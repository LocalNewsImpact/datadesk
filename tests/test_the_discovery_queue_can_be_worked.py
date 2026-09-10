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

import re
from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from explorer.models import CandidateLink, Dataset, Source, UrlVerification
from review import discovery
from review.models import ReviewDecision


def _model_cell():
    """The 'What the model said' cell, comments stripped.

    Read as text: the faults were in what rendered, and a template
    rendered through the ORM needs a database this test does not want.
    """
    import re
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[1] / "templates/review/discovery.html"
    ).read_text()
    cell = html[html.index('data-label="What the model said"') :]
    cell = cell[: cell.index("</td>")]
    cell = re.sub(r"{% comment %}.*?{% endcomment %}", "", cell, flags=re.S)
    # Tooltips are opt-in detail, not what the column reads as. Stripped
    # by attribute rather than by slicing to the first quote: the cell
    # opens with `data-label="..."`, so slicing found that instead and
    # every "not shown" assertion passed over the whole cell.
    return re.sub(r'\stitle="[^"]*"', "", cell)


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
    # Nothing in common. The story list once carried `other` for the
    # unanswerable case; leaving the box empty says that now, and says it
    # better -- "an ordinary story" is an answer, not a shrug.
    assert not story & not_story
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
        assert choice["value"] in known, choice["value"]


def test_the_story_list_holds_only_what_changes_the_outcome():
    """Every kind offered has to change what the pipeline does with the
    article, or it is a question asked for nothing.

    `news` is the one that mattered: offering it as the shortest way to
    say "it is a story" is how a sports story gets labelled `news` -- a
    category invented by the list rather than observed.
    """
    from lnic_contracts import discovery_verdict

    offered = {c["value"] for c in discovery.STORY_KINDS}
    # `news` is the one that must not be here: offering it as the
    # shortest way to say "it is a story" is how a sports story gets
    # labelled news.
    assert "news" not in offered
    # Every other kind offered is one the pipeline filters on. Three
    # decide a status outright; wire and column are handled downstream
    # without one, so the rule is "the pipeline acts on it", not "it
    # sets a status".
    assert offered == {"obituary", "opinion", "weather", "column", "wire"}
    for kind in ("obituary", "opinion", "weather"):
        note = discovery_verdict.build(verdict=discovery_verdict.IS_A_STORY, kind=kind)
        assert discovery_verdict.status_for(note) == kind


def test_it_is_a_story_submits_without_a_category(client, reviewer, crawler_schema):
    """One click for the ordinary story. Requiring a choice is what put a
    category on stories nobody had categorised."""
    from lnic_contracts import discovery_verdict

    link = _link(crawler_schema, "plain", status="not_article")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    client.post(
        reverse("review:discovery"),
        {
            "d-plain": discovery.IT_IS_A_STORY,
            f"v-plain-{discovery.IT_IS_A_STORY}": "",
            "stratum-plain": discovery.DOUBTFUL,
            "probability-plain": "1.0",
        },
    )
    link.refresh_from_db()
    assert link.status == discovery_verdict.RESTORED_STATUS, "it did not submit"
    note = (link.meta or {})[discovery_verdict.METADATA_KEY]
    assert note["kind"] == "", "a category was invented"
    assert discovery_verdict.status_for(note) is None, "the pipeline must classify it"


def test_a_rejection_still_needs_to_say_what_it_is(client, reviewer, crawler_schema):
    """A count of which kind, against a rule or a publisher, is what a fix
    gets built from -- so the other verb still requires its value."""
    story, not_story = (
        discovery.DISCOVERY_QUEUE.verb(discovery.IT_IS_A_STORY),
        discovery.DISCOVERY_QUEUE.verb(discovery.NOT_A_STORY),
    )
    assert story.takes_value and not story.value_required
    assert not_story.takes_value and not_story.value_required


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
    # The page used to print "4th percentile" beside a raw log-odds
    # score, which is the ranking and the thing ranked with nothing
    # saying so. The percentile is still what decides the wording -- it
    # is now read FOR the reader rather than at them.
    assert "a story" in body
    # Not a probability, which is the claim nothing here can support.
    # Read against the visible text: `probability-<stratum>` is a hidden
    # field carrying the inclusion probability the sampling design needs,
    # and that is a different thing said to a different audience.
    visible = re.sub(r"<[^>]+>", " ", body)
    for cannot_support in ("% likely", "probability", "confidence that"):
        assert cannot_support not in visible, cannot_support
    # The raw score belongs in the tooltip, not the column.
    assert "margin 5.0" not in re.sub(r'\stitle="[^"]*"', "", body)


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


def test_the_counts_fall_as_rows_are_answered(client, reviewer, crawler_schema):
    """The chip is the number a reviewer works down.

    It was `len(drawn)`, taken before the answered rows were removed, so
    a stratum said 746 and went on saying 746 however many had been
    decided -- and the page below it emptied while the count stood still.
    """
    for n in range(3):
        link = _link(crawler_schema, f"count{n}")
        _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)

    def doubtful_count():
        response = client.get(reverse("review:discovery"))
        return next(
            s["count"]
            for s in response.context["strata"]
            if s["key"] == discovery.DOUBTFUL
        )

    assert doubtful_count() == 3
    client.post(
        reverse("review:discovery"),
        {
            "d-count0": discovery.NOT_A_STORY,
            f"v-count0-{discovery.NOT_A_STORY}": "homepage",
            "stratum-count0": discovery.DOUBTFUL,
            "probability-count0": "1.0",
        },
    )
    assert doubtful_count() == 2


def _answer(client, link_id, verb, value):
    client.post(
        reverse("review:discovery"),
        {
            f"d-{link_id}": verb,
            f"v-{link_id}-{verb}": value,
            f"stratum-{link_id}": discovery.DOUBTFUL,
            f"probability-{link_id}": "1.0",
        },
    )


def _doubtful(client, **query):
    response = client.get(reverse("review:discovery"), query)
    return next(
        s["count"] for s in response.context["strata"] if s["key"] == discovery.DOUBTFUL
    )


def test_asking_for_any_shows_answered_rows_again(client, reviewer, crawler_schema):
    link = _link(crawler_schema, "seen")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    _answer(client, "seen", discovery.NOT_A_STORY, "homepage")
    assert _doubtful(client) == 0, "an answered row is not still waiting"
    assert _doubtful(client, decision="any") == 1


def test_the_decision_filter_asks_which_way(client, reviewer, crawler_schema):
    """ "Including decided" could only be on or off, so there was no way to
    ask the question a reviewer asks afterwards: what did I mark, and was
    I right."""
    for name in ("kept", "dropped"):
        link = _link(crawler_schema, name)
        _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    _answer(client, "kept", discovery.IT_IS_A_STORY, "news")
    _answer(client, "dropped", discovery.NOT_A_STORY, "homepage")

    assert _doubtful(client, decision=discovery.IT_IS_A_STORY) == 1
    assert _doubtful(client, decision=discovery.NOT_A_STORY) == 1
    assert _doubtful(client, decision="any") == 2
    assert _doubtful(client) == 0


def test_the_header_carries_the_shared_controls(client, reviewer, crawler_schema):
    """All three queues wear the same header: chips, then dataset, then
    the window, then what has already been said."""
    Dataset.objects.using("crawler").create(
        id="d1", slug="mizzou", label="Mizzou Missouri State"
    )
    link = _link(crawler_schema, "header")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    body = client.get(reverse("review:discovery")).content.decode()
    assert 'class="queue-facets"' in body
    assert 'name="dataset"' in body
    assert 'name="days"' in body
    assert 'name="decision"' in body


# ------------------------------------------- the verdict reaches the crawler


def test_calling_it_a_story_records_what_kind(client, reviewer, crawler_schema):
    """The status says "fetch this again"; the verdict says what it is.

    Without the second half the pipeline re-classifies the URL with the
    model that misjudged it badly enough to put it in this queue, and a
    reviewer who said "opinion" watched the article get enriched.
    """
    from lnic_contracts import discovery_verdict

    link = _link(crawler_schema, "kindly", status="not_article")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    _answer(client, "kindly", discovery.IT_IS_A_STORY, "opinion")

    link.refresh_from_db()
    assert link.status == discovery_verdict.RESTORED_STATUS
    note = (link.meta or {})[discovery_verdict.METADATA_KEY]
    assert discovery_verdict.is_readable(note)
    assert note["kind"] == "opinion"
    # The half the crawler acts on: opinion is a status no enrichment
    # stage selects, so recording it IS the instruction not to enrich.
    assert discovery_verdict.status_for(note) == "opinion"


def test_an_ordinary_story_is_restored_without_deciding_its_status(
    client, reviewer, crawler_schema
):
    """News IS the ordinary pipeline. The verdict is still recorded -- it
    is what a reviewer said -- but it must not override the detector, or
    articles the corpus wants enriched would stop."""
    from lnic_contracts import discovery_verdict

    link = _link(crawler_schema, "ordinary", status="not_article")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    _answer(client, "ordinary", discovery.IT_IS_A_STORY, "news")

    link.refresh_from_db()
    note = (link.meta or {})[discovery_verdict.METADATA_KEY]
    assert note["kind"] == "news"
    assert discovery_verdict.status_for(note) is None


def test_not_a_story_writes_nothing_to_the_link(client, reviewer, crawler_schema):
    """The link's status already excludes it, and there will be no
    article to give a verdict to."""
    link = _link(crawler_schema, "untouched", status="not_article")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    _answer(client, "untouched", discovery.NOT_A_STORY, "homepage")

    link.refresh_from_db()
    assert link.status == "not_article"
    assert not (link.meta or {})


def test_the_verdict_does_not_trample_what_the_crawler_wrote(
    client, reviewer, crawler_schema
):
    """`meta` is the crawler's column. Writing the verdict must add a key
    to it, not replace it -- everything else in there is the record of
    what the crawler saw."""
    from lnic_contracts import discovery_verdict

    link = _link(crawler_schema, "keepmine", status="not_article")
    CandidateLink.objects.using("crawler").filter(pk=link.pk).update(
        meta={"discovered_by": "rss", "crawl_depth": 2}
    )
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    _answer(client, "keepmine", discovery.IT_IS_A_STORY, "obituary")

    link.refresh_from_db()
    assert link.meta["discovered_by"] == "rss"
    assert link.meta["crawl_depth"] == 2
    assert link.meta[discovery_verdict.METADATA_KEY]["kind"] == "obituary"


# ------------------------------------- the model, and the rule that beat it


def test_the_page_shows_what_the_model_said_not_the_verdict():
    """`storysniffer_result` is the verdict AFTER guess() applies its
    whitelist and blacklist. A URL can score +477 -- strongly a story --
    and still come back False because a rule fired on /news/archives/.

    Showing the verdict under "what the model said" made every row in
    the overruled stratum read as the model agreeing with the rejection,
    which is the opposite of why those rows are in it.
    """
    assert discovery.model_said(477.9) == "story"
    assert discovery.model_said(-119.9) == "not a story"
    assert discovery.model_said(None) is None


def test_a_row_says_the_model_disagrees_with_the_outcome():
    """Not "a rule overruled it".

    storysniffer did gate these URLs at discovery; it did not record the
    verdict -- every row in url_verifications came from the September
    backfill, previous_status null on all 236,160. The outcome is the
    missing record: a URL discovered and never carried forward was
    rejected then, because rejection is what stopped it.

    So the comparison is that decision against a rescore today, and a
    disagreement means the model changed its mind, the rules did, or a
    story was lost. Only the third is an error.
    """
    # The row that was reported: scored a story, excluded as not_article.
    assert discovery.disagrees_with_outcome(477.9, "not_article") is True
    assert discovery.disagrees_with_outcome(1571.0, "opinion") is True
    # Scored a story and kept -- no disagreement.
    assert discovery.disagrees_with_outcome(477.9, "extracted") is False
    # Scored NOT a story and excluded -- the model agrees.
    assert discovery.disagrees_with_outcome(-119.9, "not_article") is False
    assert discovery.disagrees_with_outcome(None, "not_article") is False


def test_an_obituary_front_is_a_section_front():
    """Two names for one thing split the count a fix would be built
    from."""
    offered = {c["value"] for c in discovery.NOT_STORY_KINDS}
    assert "obituary_index" not in offered
    assert "section_index" in offered


# ------------------------------------------------ and it reads as English

# The cell printed three fragments that ran together unpunctuated.
#
#     What a reviewer actually saw, twice reported:
#
#         story scores a story now; it was not kept rescored 21, 3th percentile
#         story scores a story now; it was not kept rescored 25, 4th percentile
#
#     Four faults in one line. The verdict is stated, then restated by a
#     marker meant to add the *other* half. Nothing separates the parts. The
#     ordinal suffix is hardcoded `th`, so 1, 2, 3 and 21 all come out
#     wrong. And the score is log-odds -- a scale that means nothing beside
#     a percentile printed with no indication of what it ranks.
#
#     The column exists so a reviewer can decide whether to open the page.
#     None of that helped them do it.


def test_the_verdict_is_not_said_twice():
    """The phrase carries the verdict; the marker carries the outcome."""
    cell = _model_cell()
    assert "scores a story now" not in cell, cell
    # What the marker says instead: only the half the phrase omits.
    assert "the pipeline dropped it anyway" in cell


def test_no_hardcoded_ordinal_suffix():
    """`{{ row.percentile }}th` renders 3 as `3th`."""
    assert "th percentile" not in _model_cell()


def test_the_raw_score_is_not_shown_as_a_bare_number():
    """`rescored 21` is a log-odds value with no scale attached."""
    cell = _model_cell()
    assert "rescored" not in cell, cell
    assert "verification_confidence" not in cell, cell


def test_the_strength_travels_with_the_verdict():
    """A margin of 21 is above zero and in the third percentile. Both
    are true, and "story" alone reports only the first."""
    assert discovery.how_the_model_read_it(21, 3) == "weakly a story"
    assert discovery.how_the_model_read_it(1571, 92) == "strongly a story"
    assert discovery.how_the_model_read_it(477, 60) == "a story"


def test_a_rejection_is_never_called_weak():
    """The percentile ranks the margin, so the most confident
    rejections sit at the BOTTOM of it. Reading strength off the
    percentile below zero renders them as 'weakly not a story'."""
    for percentile_rank in (0, 3, 8, 25, 50):
        assert discovery.how_the_model_read_it(-119, percentile_rank) == "not a story"


def test_an_unscored_row_says_nothing():
    assert discovery.how_the_model_read_it(None, 5) is None


def test_the_not_a_story_list_covers_what_a_crawler_produces():
    """The list started at the shapes a news site publishes and left out
    the ones a crawler produces. A dead link, a feed URL, a search result
    and a PDF are a large share of what reaches this queue, and all four
    were going in as "Other" -- a count of which says nothing a fix can
    be built from, which is the reason the list exists at all."""
    values = {choice["value"] for choice in discovery.NOT_STORY_KINDS}
    for expected in ("not_found", "feed", "search", "file"):
        assert expected in values, expected


def test_the_dead_link_and_feed_options_come_first():
    """Ordered by what a reviewer meets most, not alphabetically: a list
    whose commonest answers are ninth is a list people scroll past."""
    values = [choice["value"] for choice in discovery.NOT_STORY_KINDS]
    assert values[0] == "not_found"
    assert values[1] == "feed"
    assert values[-1] == "other", "Other belongs last, as the fallback"


def test_every_not_a_story_kind_is_a_distinct_value():
    """Two names for one thing split the count a fix would be built
    from -- the reason Obituary Front was removed."""
    values = [choice["value"] for choice in discovery.NOT_STORY_KINDS]
    assert len(values) == len(set(values))
    labels = [choice["label"] for choice in discovery.NOT_STORY_KINDS]
    assert len(labels) == len(set(labels))


def test_a_new_kind_needs_no_contract_change():
    """ "Not a story" leaves the crawler status alone -- it already
    excludes the row -- so the value is recorded on the ReviewDecision
    and nothing in lnic_contracts has to learn it. The story side is
    different: `status_for` only overrides for UNENRICHED_TYPES."""
    from lnic_contracts import discovery_verdict

    for choice in discovery.NOT_STORY_KINDS:
        note = {
            "verdict": "not_a_story",
            "kind": choice["value"],
            "decided_at": "2026-09-09T00:00:00Z",
        }
        assert discovery_verdict.status_for(note) is None


def test_a_briefs_roundup_is_not_a_story():
    """Many short items on one page, so there is no single story to
    extract -- the same shape as a section front rather than a story that
    happens to be short."""
    values = {choice["value"] for choice in discovery.NOT_STORY_KINDS}
    assert "news_briefs" in values


def test_wire_is_withheld_from_the_fetch_queue(client, reviewer, crawler_schema):
    """A reviewer who reads a URL and says "wire" has already reached the
    conclusion a fetch, an extraction and a wire check would reach. The
    kind was recorded and then ignored, so all three happened anyway."""
    link = _link(crawler_schema, "ranked")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    client.post(
        reverse("review:discovery"),
        {
            f"d-{link.id}": discovery.IT_IS_A_STORY,
            f"v-{link.id}-{discovery.IT_IS_A_STORY}": "wire",
            f"stratum-{link.id}": "ranked",
            f"probability-{link.id}": "1.0",
        },
    )
    link.refresh_from_db()
    assert link.status == "wire"
    assert link.status != "discovered", "wire went back into the fetch queue"


def test_an_ordinary_story_still_goes_back_to_the_fetch_queue(
    client, reviewer, crawler_schema
):
    """The commonest answer must keep working: withholding wire must not
    withhold everything."""
    link = _link(crawler_schema, "ranked")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    client.post(
        reverse("review:discovery"),
        {
            f"d-{link.id}": discovery.IT_IS_A_STORY,
            f"v-{link.id}-{discovery.IT_IS_A_STORY}": "",
            f"stratum-{link.id}": "ranked",
            f"probability-{link.id}": "1.0",
        },
    )
    link.refresh_from_db()
    assert link.status == "discovered"


def test_an_obituary_is_still_fetched(client, reviewer, crawler_schema):
    """Unenriched is not unfetched. An obituary is extracted and kept and
    merely not enriched, so it must still reach the fetch queue."""
    link = _link(crawler_schema, "ranked")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    client.post(
        reverse("review:discovery"),
        {
            f"d-{link.id}": discovery.IT_IS_A_STORY,
            f"v-{link.id}-{discovery.IT_IS_A_STORY}": "obituary",
            f"stratum-{link.id}": "ranked",
            f"probability-{link.id}": "1.0",
        },
    )
    link.refresh_from_db()
    assert link.status == "discovered"


def test_the_verdict_is_recorded_either_way(client, reviewer, crawler_schema):
    """Withholding the link must not lose what the reviewer said: the
    verdict in meta is what stops the pipeline re-deciding it."""
    from lnic_contracts import discovery_verdict

    link = _link(crawler_schema, "ranked")
    _verification(crawler_schema, link, 5.0, True)
    client.force_login(reviewer)
    client.post(
        reverse("review:discovery"),
        {
            f"d-{link.id}": discovery.IT_IS_A_STORY,
            f"v-{link.id}-{discovery.IT_IS_A_STORY}": "wire",
            f"stratum-{link.id}": "ranked",
            f"probability-{link.id}": "1.0",
        },
    )
    link.refresh_from_db()
    note = (link.meta or {}).get(discovery_verdict.METADATA_KEY)
    assert note is not None
    assert note["kind"] == "wire"
    assert note["verdict"] == discovery_verdict.IS_A_STORY
