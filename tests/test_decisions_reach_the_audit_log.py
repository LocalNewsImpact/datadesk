"""A decision that changed production data is in the audit log.

review/submit.py has said since it was written that "the whole session is
one audit event rather than one per row". It wrote no audit events at
all. Decisions reached the article and the ReviewDecision table, and the
log built to answer "who changed this, and to what" had nothing in it —
in a console whose whole premise is an audited write path.

One entry per session, because a reviewer reads down a page and sends
the lot: forty entries for one action is a log nobody reads.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLogEntry
from explorer.models import Article, CandidateLink, Dataset, DatasetSource, Source


@pytest.fixture
def reviewer(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    user.is_superuser = user.is_staff = True
    user.save()
    return user


@pytest.fixture
def flagged(crawler_schema):
    dataset = Dataset.objects.create(id="d1", slug="mo", label="Missouri")
    source = Source.objects.create(id="s1", host="a.example", host_norm="a.example")
    DatasetSource.objects.create(id="ds1", dataset_id=dataset.id, source_id=source.id)
    made = []
    for n in range(3):
        link = CandidateLink.objects.create(
            id=f"c{n}", source_id=source.id, url=f"https://a.example/{n}"
        )
        made.append(
            Article.objects.create(
                id=f"a{n}",
                candidate_link=link,
                title=f"story {n}",
                status="not_article",
                wire_check_status="complete",
                content="A captured body worth reading.",
                text="A captured body worth reading.",
                author="Ellen Reporter",
                publish_date=timezone.now(),
                created_at=timezone.now(),
                enrichment_attempts=0,
            )
        )
    return made


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_session_of_decisions_is_recorded(client, reviewer, flagged):
    client.force_login(reviewer)
    client.post(
        reverse("review:queue"),
        {"d-a0": "restore", "d-a1": "restore"},
    )

    entry = AuditLogEntry.objects.get()
    assert entry.actor == reviewer
    assert entry.action == "review.extraction.decide"
    assert entry.target_table == "article"
    assert sorted(entry.target_ids) == ["a0", "a1"]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_entry_says_what_each_row_became(client, reviewer, flagged):
    """A log that records only the ids answers "who touched these" and
    not "what did they do", which is the question a revert needs."""
    client.force_login(reviewer)
    client.post(reverse("review:queue"), {"d-a0": "restore"})

    entry = AuditLogEntry.objects.get()
    after = {row["id"]: row for row in entry.after}
    assert after["a0"]["verb"] == "restore"
    assert after["a0"]["status"] == "cleaned"
    before = {row["id"]: row for row in entry.before}
    assert before["a0"]["status"] == "not_article"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_one_entry_per_session_not_one_per_row(client, reviewer, flagged):
    client.force_login(reviewer)
    client.post(
        reverse("review:queue"),
        {"d-a0": "restore", "d-a1": "restore", "d-a2": "restore"},
    )
    assert AuditLogEntry.objects.count() == 1
    assert len(AuditLogEntry.objects.get().target_ids) == 3


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_session_that_decided_nothing_writes_nothing(client, reviewer, flagged):
    """Opening the page and sending it back is not an action on the
    data."""
    client.force_login(reviewer)
    client.post(reverse("review:queue"), {})
    assert not AuditLogEntry.objects.exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_entry_is_visible_on_the_audit_page(client, reviewer, flagged):
    """The log exists to be read. An entry the page does not show is the
    same as no entry."""
    client.force_login(reviewer)
    client.post(reverse("review:queue"), {"d-a0": "restore"})

    body = client.get(reverse("review:audit_log")).content.decode()
    assert "review.extraction.decide" in body
