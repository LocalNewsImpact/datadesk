"""Nothing should be able to stop an article silently.

Every defect found on 2026-09-08 had the same shape: a stage decided, or
failed to, and said nothing a reader could see. A link paused on 403s, a
source switched off so both extraction selectors skip it without an
error, a body stored as ciphertext, a run waiting on a suspended cron --
each ends with nothing in the export and nothing saying why.

This page is the answer to "why has nothing happened", and it is not a
queue: a 404 is not a judgement somebody accepts or rejects.
"""

import pytest
from django.urls import reverse

from explorer import blocked as blockages


@pytest.fixture
def an_editor(db):
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant

    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")
    return user


def test_every_check_says_why_it_matters():
    """A count with no explanation is a number nobody can act on."""
    for _group, label, why, sql in blockages.CHECKS:
        assert label, "a check with no name"
        assert why.strip().endswith("."), f"{label}: the reason reads as a sentence"
        assert sql.strip().upper().startswith("SELECT"), label


def test_the_groups_are_the_stages_a_record_dies_at():
    groups = {group for group, _l, _w, _s in blockages.CHECKS}
    assert groups == {
        blockages.NEVER_FETCHED,
        blockages.FETCH_FAILED,
        blockages.STALLED,
        blockages.BODY_UNUSABLE,
        blockages.INCONSISTENT,
        blockages.WAITING,
    }


def test_a_paused_link_that_was_fetched_is_not_counted_as_never_fetched():
    """7,360 links are paused with no reason and only 1,142 were never
    fetched; 19,683 are paused on 403s and only 3,362 were. The rest have
    an article, most of them finished. Counting the totals read 26,918
    never-fetched where the true figure is 8,407."""
    for group, label, _why, sql in blockages.CHECKS:
        if group == blockages.NEVER_FETCHED and "Paused" in label:
            assert "NOT EXISTS" in sql, f"{label}: counts links that were fetched"


def test_bookkeeping_is_not_counted_as_a_blockage():
    """20,209 links say paused while their article is enriched or
    labelled. One of the two rows is wrong, but nothing is stopped."""
    inconsistent = [c for c in blockages.CHECKS if c[0] == blockages.INCONSISTENT]
    assert inconsistent, "the group exists"
    for _g, _label, why, _sql in inconsistent:
        assert "blocked" in why.lower()


def test_a_backlog_is_not_counted_as_a_failure():
    """85,708 articles are labelled and waiting on a suspended cron. That
    is the largest number on the page and it is not broken, so the total
    a reader is asked to act on must not include it."""
    waiting = [c for c in blockages.CHECKS if c[0] == blockages.WAITING]
    assert waiting, "the waiting group exists"
    for _g, _label, why, _sql in waiting:
        assert "cron" in why or "waiting" in why or "queued" in why


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_answers_without_a_crawler(client, an_editor):
    """The crawler alias is empty in tests unless a fixture builds it, so
    this is the degraded path: it must say so rather than 500."""
    client.force_login(an_editor)
    response = client.get(reverse("explorer:blocked"))
    assert response.status_code == 200
    assert b"not connected" in response.content


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_reports_what_it_finds(client, an_editor, crawler_schema):
    from explorer.models import Article, CandidateLink, Source

    source = Source.objects.create(
        id="s1", host="a.example", host_norm="a.example", canonical_name="A Paper"
    )
    CandidateLink.objects.create(
        id="cl-403",
        url="https://a.example/1",
        source=source,
        status="paused",
        error_message="Auto-paused: multiple HTTP 403 responses",
    )
    link = CandidateLink.objects.create(
        id="cl-2", url="https://a.example/2", source=source, status="extracted"
    )
    Article.objects.create(
        id="a-rot47",
        candidate_link=link,
        status="labeled",
        wire_check_status="complete",
        content="Some prose k^Am more prose",
    )

    client.force_login(an_editor)
    body = client.get(reverse("explorer:blocked")).content.decode()
    assert "Paused after repeated 403s" in body
    assert "Body is still ROT47 ciphertext" in body
    # The publisher is the unit wherever a publisher explains it: 6,467
    # West Plains links did not each fail on their own.
    assert "A Paper" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_blockage_no_publisher_explains_says_so(client, an_editor, crawler_schema):
    """`in_review` is a person's decision, not a site's behaviour, and
    offering a publisher column for it would invite a wrong question."""
    from explorer.models import Article, CandidateLink, Source

    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    link = CandidateLink.objects.create(
        id="cl-1", url="https://a.example/1", source=source, status="extracted"
    )
    Article.objects.create(
        id="a-held",
        candidate_link=link,
        status="in_review",
        wire_check_status="complete",
        content="A body.",
    )
    client.force_login(an_editor)
    body = client.get(reverse("explorer:blocked")).content.decode()
    assert "Held for review" in body
    assert "not one publisher's" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_nothing_blocked_is_said_plainly(client, an_editor, crawler_schema):
    client.force_login(an_editor)
    body = client.get(reverse("explorer:blocked")).content.decode()
    assert "Nothing is blocked." in body
