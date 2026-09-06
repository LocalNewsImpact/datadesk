"""Reverting is a decision, and the log did not carry the facts for it.

The audit log offered a Revert button beside an action name, a table, a
row count and sixty characters of reason. Three things it never showed
decide the answer:

- what the entry changed, field by field;
- whether the rows still hold what it wrote -- where they do not, a
  revert discards whatever moved them since;
- whether the entry can be reverted at all. Half the log is written on
  tables outside the write boundary, where the button raised.

And whether an entry had already been reverted was a question about the
wording of free text, because the link was prose rather than a row.
"""

from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry
from explorer.models import Article, CandidateLink, Source
from review import audit_entries
from review.services import audited_update, revert

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def editor(client):
    """Admin, because the log is an Admin section -- and admin carries
    `write`, so the same person can revert."""
    user = User.objects.create_user("editor", email="editor@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
    client.force_login(user)
    return user


@pytest.fixture
def article(crawler_schema):
    source = Source.objects.create(id="s1", host="t.example", host_norm="t.example")
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=source)
    return Article.objects.create(
        id="a1",
        candidate_link=link,
        title="Council picks a chair",
        author="Jane Doe",
        content="The vote was unanimous.",
        status="labeled",
        wire_check_status="complete",
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
    )


def _entry(editor, article, **changes):
    return audited_update(
        editor, [article], changes or {"author": "Wrong"}, "edit:author"
    )


# --- what the entry did -----------------------------------------------------


def test_the_page_shows_the_values_the_entry_changed(client, editor, article):
    entry = _entry(editor, article)
    content = client.get(f"/review/audit/{entry.pk}/").content.decode()
    assert "Jane Doe" in content  # what the row held
    assert "Wrong" in content  # what was written


def test_every_target_row_is_named_and_linked(client, editor, article):
    entry = _entry(editor, article)
    content = client.get(f"/review/audit/{entry.pk}/").content.decode()
    assert "/explorer/articles/a1/" in content


def test_the_log_links_to_the_entry_instead_of_reverting_from_the_row(
    client, editor, article
):
    """A revert asked for from a list row is asked for without the facts."""
    entry = _entry(editor, article)
    content = client.get("/review/audit/").content.decode()
    assert f"/review/audit/{entry.pk}/" in content
    assert f"/review/audit/{entry.pk}/revert/" not in content


# --- whether the change still stands ----------------------------------------


def test_a_row_changed_since_is_reported_as_such(client, editor, article):
    """Reverting over later work is the case the page exists to catch."""
    entry = _entry(editor, article)
    audited_update(editor, [article], {"author": "Someone Else"}, "edit:author")

    detail = audit_entries.detail(AuditLogEntry.objects.get(pk=entry.pk))
    field = detail["rows"][0]["fields"][0]
    assert field["now"] == "Someone Else"
    assert field["drifted"] is True

    content = client.get(f"/review/audit/{entry.pk}/").content.decode()
    assert "changed since" in content


def test_an_unchanged_row_is_not_reported_as_drifted(editor, article):
    entry = _entry(editor, article)
    detail = audit_entries.detail(AuditLogEntry.objects.get(pk=entry.pk))
    assert detail["rows"][0]["fields"][0]["drifted"] is False


def test_a_row_that_is_gone_is_named(client, editor, article):
    entry = _entry(editor, article)
    Article.objects.filter(id="a1").delete()
    detail = audit_entries.detail(AuditLogEntry.objects.get(pk=entry.pk))
    assert detail["missing"] == ["a1"]
    content = client.get(f"/review/audit/{entry.pk}/").content.decode()
    assert "no longer present" in content


# --- whether it can be reverted ---------------------------------------------


def test_an_entry_outside_the_write_boundary_offers_no_revert(client, editor):
    """`auth_user`, `accounts_invitation` and `visuals` are written by
    paths that are not the audited write path, and `revert()` raises on
    them. The list offered the button anyway."""
    entry = AuditLogEntry.objects.create(
        actor=editor,
        action="accounts:role_change",
        target_table="auth_user",
        target_ids=[str(editor.pk)],
        before={"role": "viewer"},
        after={"role": "editor"},
    )
    detail = audit_entries.detail(entry)
    assert detail["revertable"] is False
    assert detail["shape"] == audit_entries.NOTE

    content = client.get(f"/review/audit/{entry.pk}/").content.decode()
    assert "outside the write boundary" in content
    assert f"/review/audit/{entry.pk}/revert/" not in content


def test_a_revertable_entry_offers_the_form(client, editor, article):
    entry = _entry(editor, article)
    content = client.get(f"/review/audit/{entry.pk}/").content.decode()
    assert f"/review/audit/{entry.pk}/revert/" in content


# --- what a revert leaves behind --------------------------------------------


def test_a_reverted_entry_says_which_entry_undid_it(client, editor, article):
    entry = _entry(editor, article)
    compensating = revert(editor, entry, reason="a reason of my own")

    assert compensating.reverts_id == entry.pk
    detail = audit_entries.detail(AuditLogEntry.objects.get(pk=entry.pk))
    assert detail["revertable"] is False
    assert detail["reverted_by"].pk == compensating.pk

    content = client.get(f"/review/audit/{entry.pk}/").content.decode()
    assert "Already reverted" in content
    assert f"/review/audit/{compensating.pk}/" in content


def test_the_compensating_entry_says_what_it_undid(client, editor, article):
    entry = _entry(editor, article)
    compensating = revert(editor, entry)
    content = client.get(f"/review/audit/{compensating.pk}/").content.decode()
    assert f"entry {entry.pk}" in content


def test_reverting_returns_to_the_entry_with_the_outcome(client, editor, article):
    entry = _entry(editor, article)
    response = client.post(
        f"/review/audit/{entry.pk}/revert/", {"reason": "wrong byline"}, follow=True
    )
    article.refresh_from_db()
    assert article.author == "Jane Doe"
    assert response.redirect_chain[-1][0] == f"/review/audit/{entry.pk}/"
    assert "Reverted" in response.content.decode()


def test_a_revert_the_boundary_refuses_is_said_not_raised(client, editor):
    """The button used to raise a 500 on half the log."""
    entry = AuditLogEntry.objects.create(
        actor=editor,
        action="accounts:role_change",
        target_table="auth_user",
        target_ids=[str(editor.pk)],
        before={str(editor.pk): {"role": "viewer"}},
        after={str(editor.pk): {"role": "editor"}},
    )
    response = client.post(f"/review/audit/{entry.pk}/revert/", follow=True)
    assert response.status_code == 200
    assert "not writable" in response.content.decode()


# --- access ------------------------------------------------------------------


def test_the_entry_is_admin_only(client, article):
    """The log is an Admin section (SCOPE.md §2.2) and so is one entry
    of it."""
    editor = User.objects.create_user("ed2", email="ed2@localnewsimpact.org")
    Grant.objects.create(user=editor, app=DATADESK, scope="", role="editor")
    entry = audited_update(editor, [article], {"author": "Wrong"}, "edit:author")
    client.force_login(editor)
    assert client.get(f"/review/audit/{entry.pk}/").status_code == 403
