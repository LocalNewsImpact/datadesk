"""The extraction queue works the way the sources queue works.

It held a dense grid and three inert buttons under a note saying
dispositions would arrive later. It now offers a verb per row, marked on
the way down the page and submitted together -- the same shape, the same
chips, the same dock.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source
from review.models import ReviewDecision

URL = reverse("review:queue")


@pytest.fixture
def reviewer(client, db):
    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    client.force_login(user)
    return user


@pytest.fixture
def rows(crawler_schema):
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(
        id="s1", host="a.example", host_norm="a.example", canonical_name="The Paper"
    )
    DatasetSource.objects.create(id="ds1", dataset=dataset, source_id=source.id)

    def article(pk, **kwargs):
        link = CandidateLink.objects.create(id=f"c{pk}", source_id=source.id, url="u")
        return Article.objects.create(
            id=pk,
            candidate_link=link,
            status="not_article",
            wire_check_status="complete",
            **kwargs,
        )

    keeps_body = article(
        "with-body",
        title="A real story",
        content="A body that was captured.",
        author="Jo Reporter",
    )
    archived = article(
        "no-body",
        title="Body was dropped",
        content="",
        text="",
        raw_gcs_path="gs://bucket/page.html.gz",
    )
    gone = article("nothing-left", title="Nothing left", content="", text="")
    return keeps_body, archived, gone


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_row_with_a_body_offers_reject(client, reviewer, rows):
    body = client.get(URL, {"all": "1"}).content.decode()
    assert 'data-verb="reject"' in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_row_with_no_body_offers_re_extraction_instead(client, reviewer, rows):
    """The capture is re-parsed, not re-crawled, and only while the
    30-day archive still holds the page."""
    body = client.get(URL, {"all": "1"}).content.decode()
    assert 'data-verb="reextract"' in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_every_row_can_at_least_be_accepted(client, reviewer, rows):
    body = client.get(URL, {"all": "1"}).content.decode()
    assert body.count('data-verb="accept"') == 3


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_uses_the_shared_chips_and_dock(client, reviewer, rows):
    """The same controls as the sources queue rather than a second
    vocabulary for the same job."""
    body = client.get(URL, {"all": "1"}).content.decode()
    assert 'class="queue-facets"' in body
    assert 'class="queue-dock"' in body
    assert 'id="queue-form"' in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_submitting_a_decision_records_it(client, reviewer, rows):
    client.post(URL, {"d-with-body": "accept"})
    entry = ReviewDecision.objects.get(subject_id="with-body")
    assert entry.verb == "accept"
    assert entry.claim == "not_article"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_rejecting_rewinds_the_status(client, reviewer, rows):
    client.post(URL, {"d-with-body": "reject"})
    assert Article.objects.get(id="with-body").status == "cleaned"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_verb_the_row_cannot_carry_out_is_refused(client, reviewer, rows):
    """Refused rather than ignored. Reaching the write path it would have
    reported success and done nothing: there is no body to hand back."""
    client.post(URL, {"d-nothing-left": "reject"})
    assert not ReviewDecision.objects.filter(subject_id="nothing-left").exists()
    assert Article.objects.get(id="nothing-left").status == "not_article"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_submission_carrying_nothing_says_so(client, reviewer, rows):
    """Said, not swallowed: a queue coming back with the same questions
    is the only evidence either way."""
    response = client.post(URL, {})
    # The fact, not the shape. Every queue's receipt is one shape now
    # (review/submit.py), so asserting a literal dict here would freeze
    # a detail this test does not care about and every queue shares.
    receipt = client.session["queue_receipt"]
    assert receipt["nothing"] is True
    assert receipt["decided"] == 0
    assert response.status_code == 302
