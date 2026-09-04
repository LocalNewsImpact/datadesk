"""The extraction queue works the way the proposals queue works.

It was built with the proposals queue's markup -- `.prop` rows, `.verb`
buttons, a hidden `d-<id>` per row, a dock with tallies and a Submit
button -- and none of its behaviour:

- No script. The inline session script lives in proposals.html, so
  pressing a verb here recorded nothing, the tallies stayed at zero, and
  Submit was disabled with nothing able to enable it. The queue could be
  read and not worked.
- No receipt. `_submit_queue_decisions` writes one to the session and
  nothing read it, so a submitted session came back looking untouched --
  which is also what a submission that did nothing looks like.
- No end to the asking. `accept` writes nothing to the article, because
  its status already excludes it, so an accepted article went on matching
  its case and was asked about on every visit. `answered_questions`
  existed for this and nothing called it.
- No decided state. There was no way to see what had been decided.
"""

import json

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from explorer.models import (
    Article,
    CandidateLink,
    ContentTypeDetection,
    Dataset,
    DatasetSource,
    Source,
)
from review import queue as review_queue
from review.dispositions import EXTRACTION, record
from review.models import ExtractionDecision


@pytest.fixture
def reviewer(db):
    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    user.is_superuser = True
    user.is_staff = True
    user.save()
    return user


@pytest.fixture
def flagged(crawler_schema):
    """One article the queue holds, in a dataset."""
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    link = CandidateLink.objects.create(id="c1", source_id=source.id, url="u")
    return Article.objects.create(
        id="a1",
        candidate_link=link,
        title="A story flagged as not an article",
        status="not_article",
        wire_check_status="complete",
        content="A captured body long enough to be worth doubting.",
        text="A captured body long enough to be worth doubting.",
        # A byline that survived is one of the reasons `doubtful_q` keeps
        # a `not_article`, and the landing view shows only doubtful rows.
        author="Ellen Reporter",
        enrichment_attempts=0,
    )


# --- the session can be worked at all ----------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_loads_the_script_that_records_a_decision(client, reviewer, flagged):
    """Without it every verb on the page is inert."""
    client.force_login(reviewer)
    body = client.get(reverse("review:queue")).content.decode()
    assert (
        "js/review-queue.js" in body
    ), "the queue renders verbs and a Submit button with no script to work them"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_proposals_queue_loads_the_same_script(client, reviewer, crawler_schema):
    """One behaviour, not two. A change to how a decision is recorded
    must not reach one queue and not the other."""
    client.force_login(reviewer)
    body = client.get(reverse("review:proposals")).content.decode()
    assert "js/review-queue.js" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_dock_names_the_verbs_the_script_counts(client, reviewer, flagged):
    """The tallies are found by `data-tally`, matching the verb recorded
    in the hidden field. A tally without one silently stays at zero."""
    client.force_login(reviewer)
    body = client.get(reverse("review:queue")).content.decode()
    for verb in ("accept", "reject", "reextract"):
        assert f'data-tally="{verb}"' in body


# --- the row is a record, not a table cell -----------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_article_is_a_card_with_its_evidence(client, reviewer, flagged):
    """The proposals queue's shape: the thing being judged is a record
    with a heading, and the evidence is beside the decision rather than
    ten columns away in a row that scrolls sideways."""
    client.force_login(reviewer)
    body = client.get(reverse("review:queue")).content.decode()
    assert 'class="rec"' in body
    assert 'data-record="a1"' in body
    assert "A story flagged as not an article" in body
    for fact in ("Flagged as", "Captured", "CIN label", "Byline", "Wire"):
        assert fact in body, f"the card does not show {fact}"
    assert 'data-verb="accept"' in body


# --- a settled question is not asked again -----------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_accepted_article_leaves_the_queue(reviewer, flagged):
    """`accept` writes nothing to the article, so nothing about the
    article itself stops it matching its case again."""
    assert flagged.id in {a.id for a in review_queue.queued({}, reviewer)}
    record(flagged, decision="accept", stage=EXTRACTION, user=reviewer)
    assert flagged.id not in {a.id for a in review_queue.queued({}, reviewer)}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_new_claim_about_the_same_article_is_asked(reviewer, flagged):
    """A decision settles one claim, not the article. An article whose
    status later changes raises a question nobody has answered."""
    record(flagged, decision="accept", stage=EXTRACTION, user=reviewer)
    # Re-flagged, as an obituary the detector was barely confident of --
    # a claim nobody has answered.
    Article.objects.filter(id=flagged.id).update(status="obituary")
    ContentTypeDetection.objects.create(
        article_id=flagged.id,
        detected_type="obituary",
        confidence_score=0.17,
        evidence=json.dumps({"content": ["passed away"]}),
    )
    assert flagged.id in {a.id for a in review_queue.queued({}, reviewer)}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_including_decided_shows_it_again(reviewer, flagged):
    record(flagged, decision="accept", stage=EXTRACTION, user=reviewer)
    shown = {a.id for a in review_queue.queued({"state": "all"}, reviewer)}
    assert flagged.id in shown


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_decided_article_is_shown_decided_not_offered_again(
    client, reviewer, flagged
):
    """With live buttons it would look undecided, and the click would be
    dropped -- `_submit_queue_decisions` refuses a verb the row cannot
    carry out, so a person would believe they had changed something."""
    record(flagged, decision="accept", stage=EXTRACTION, user=reviewer)
    client.force_login(reviewer)
    body = client.get(reverse("review:queue"), {"state": "all"}).content.decode()
    assert "decided-outcome" in body
    assert 'name="d-a1"' not in body, "a decided row still offers a decision"


# --- the receipt -------------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_submitted_session_says_what_it_did(client, reviewer, flagged):
    client.force_login(reviewer)
    response = client.post(reverse("review:queue"), {"d-a1": "accept"}, follow=True)
    body = response.content.decode()
    assert "Submitted:" in body
    assert "1 accepted" in body
    assert ExtractionDecision.objects.filter(article_id="a1").count() == 1


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_session_that_decided_nothing_says_so(client, reviewer, flagged):
    """Rather than redirecting in silence, where "the page lost my
    decisions" and "it worked" look the same."""
    client.force_login(reviewer)
    response = client.post(reverse("review:queue"), {}, follow=True)
    assert "Nothing was submitted" in response.content.decode()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_receipt_is_shown_once(client, reviewer, flagged):
    """It is popped from the session. A receipt that persisted would
    report the last submission on every later visit."""
    client.force_login(reviewer)
    client.post(reverse("review:queue"), {"d-a1": "accept"}, follow=True)
    again = client.get(reverse("review:queue")).content.decode()
    assert "Submitted:" not in again


# --- the decision has to reach the pipeline ----------------------------------
#
# The console's own record stops the console asking. It does not stop the
# crawler: the hold is raised from the article's own fields, so a claim
# answered here is raised again by the next run that reads those fields --
# held, released from the console, held again, with the decision undone by
# a stage that never knew it was made.


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_decision_is_written_onto_the_article(reviewer, flagged):
    """On the row, because the two databases do not join. It is the only
    place the crawler and the console can both see it."""
    from lnic_contracts import review_note as contract

    record(flagged, decision="accept", stage=EXTRACTION, user=reviewer)
    flagged.refresh_from_db()
    assert contract.is_answered(
        flagged.metadata, claim="not_article", stage=EXTRACTION
    ), "the crawler cannot see that this was answered, so it will ask again"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_crawler_reads_what_the_console_wrote(reviewer, flagged):
    """The two halves, run against each other. Either side alone can pass
    its own tests while the pair disagrees, which is what the shared
    contract exists to prevent -- so the assertion is that the crawler's
    own rule, given the console's own output, does not hold the article."""
    from lnic_contracts import review_note as contract

    record(flagged, decision="accept", stage=EXTRACTION, user=reviewer)
    flagged.refresh_from_db()

    # src/pipeline/review_hold.apply_hold, in the form this repository can
    # state without importing the crawler: a claim with an answer is
    # dropped before anything is held.
    claims = ["not_article"]
    unanswered = [
        claim
        for claim in claims
        if not contract.is_answered(flagged.metadata, claim=claim, stage=EXTRACTION)
    ]
    assert unanswered == [], "the crawler would hold an article a person released"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_decision_does_not_flatten_the_metadata_the_crawler_keeps(reviewer, flagged):
    """The crawler keeps the hold note in the same column. Writing a bare
    object over it would strand every held article."""
    from lnic_contracts import review_note as contract

    Article.objects.filter(id=flagged.id).update(
        metadata={"pause_reason": "housekeeping", "cohort": "mo"}
    )
    flagged.refresh_from_db()
    record(flagged, decision="accept", stage=EXTRACTION, user=reviewer)
    flagged.refresh_from_db()
    assert flagged.metadata["pause_reason"] == "housekeeping"
    assert flagged.metadata["cohort"] == "mo"
    assert contract.is_answered(flagged.metadata, claim="not_article", stage=EXTRACTION)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_write_path_is_granted_the_column_it_writes():
    """Postgres enforces the boundary, so a column the console writes and
    the role cannot must fail here rather than in production."""
    from django.conf import settings

    grants = (settings.BASE_DIR / "infra/sql/create_crawler_write_role.sql").read_text()
    articles = grants[grants.index("ON articles TO datadesk_rw") - 400 :]
    articles = articles[: articles.index("ON articles TO datadesk_rw")]
    assert "metadata" in articles, (
        "record() writes articles.metadata; datadesk_rw is not granted it, "
        "so the write fails in production and passes here"
    )
