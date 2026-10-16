"""A body the capture got wrong can be sent back to be fetched.

On 2026-10-16 the extraction queue showed five KHQ stories by Kevin Teeter at
0 characters, flagged minimal_capture, offering Accept and nothing else --
and Accept keeps a real story out of the export. Their pages are fine; their
captures were not. The fix is a new request, which only the crawler can make.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from explorer.models import Article, CandidateLink, Source
from review.dispositions import (
    ACCEPT,
    EXTRACTION,
    FETCH_AGAIN,
    FETCH_LOGGED_IN,
    REFETCH_LINK_STATUS,
    record,
    verbs_for,
)
from review.models import ReviewDecision


@pytest.fixture
def reviewer(db):
    return User.objects.create_user("ed", email="ed@localnewsimpact.org")


def _article(pk, *, logs_in=False, link_status="not_article", **kwargs):
    host = "yakimaherald.com" if logs_in else "khq.com"
    source, _ = Source.objects.get_or_create(
        id=f"s-{host}",
        defaults={"host": host, "host_norm": host, "requires_login": logs_in},
    )
    link = CandidateLink.objects.create(
        id=f"c{pk}", source_id=source.id, url=f"https://{host}/{pk}", status=link_status
    )
    fields = {
        "status": "not_article",
        "wire_check_status": "complete",
        "raw": "",
        "text": "",
    }
    fields.update(kwargs)
    return Article.objects.create(id=pk, candidate_link=link, **fields)


@pytest.mark.django_db(databases=["default", "crawler"])
class TestWhatIsOffered:
    def test_a_body_less_row_is_no_longer_accept_only(self, crawler_schema):
        article = _article("k1")
        assert verbs_for(article, EXTRACTION) == [ACCEPT, FETCH_AGAIN]

    def test_a_credentialed_publisher_is_offered_the_logged_in_fetch(
        self, crawler_schema
    ):
        verbs = verbs_for(_article("y1", logs_in=True), EXTRACTION)
        assert FETCH_LOGGED_IN in verbs
        assert FETCH_AGAIN not in verbs

    def test_a_row_with_a_body_is_offered_it_beside_the_others(self, crawler_schema):
        verbs = verbs_for(_article("k2", raw="A whole story."), EXTRACTION)
        assert verbs[:3] == ["accept", "restore", "reject"]
        assert FETCH_AGAIN in verbs

    def test_a_link_already_waiting_is_not_offered_it_again(self, crawler_schema):
        article = _article("k3", link_status=REFETCH_LINK_STATUS)
        assert FETCH_AGAIN not in verbs_for(article, EXTRACTION)


@pytest.mark.django_db(databases=["default", "crawler"])
class TestWhatItWrites:
    def test_the_link_is_rewound_and_the_note_says_who_and_why(
        self, reviewer, crawler_schema
    ):
        article = _article("k4")
        record(article, decision=FETCH_AGAIN, stage=EXTRACTION, user=reviewer)
        article.refresh_from_db()
        link = CandidateLink.objects.get(id="ck4")
        assert link.status == REFETCH_LINK_STATUS
        note = article.metadata["refetch"]
        assert note["by"] == "ed@localnewsimpact.org"
        assert note["reason"]
        assert note["previous_link_status"] == "not_article"
        assert note["logged_in"] is False

    def test_the_status_is_left_for_the_new_capture_to_earn(
        self, reviewer, crawler_schema
    ):
        article = _article("k5")
        record(article, decision=FETCH_AGAIN, stage=EXTRACTION, user=reviewer)
        article.refresh_from_db()
        assert article.status == "not_article"

    def test_the_logged_in_fetch_is_noted_as_one(self, reviewer, crawler_schema):
        article = _article("y2", logs_in=True)
        record(article, decision=FETCH_LOGGED_IN, stage=EXTRACTION, user=reviewer)
        article.refresh_from_db()
        assert article.metadata["refetch"]["logged_in"] is True
        assert CandidateLink.objects.get(id="cy2").status == REFETCH_LINK_STATUS

    def test_the_decision_says_what_was_asked_of_the_crawler(
        self, reviewer, crawler_schema
    ):
        article = _article("k6")
        record(article, decision=FETCH_AGAIN, stage=EXTRACTION, user=reviewer)
        decision = ReviewDecision.objects.get(subject_id="k6")
        assert decision.verb == FETCH_AGAIN
        assert decision.wrote["link_status"] == REFETCH_LINK_STATUS
        assert decision.wrote["previous_link_status"] == "not_article"
