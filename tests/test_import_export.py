"""The import protocol (upload → map → diff → apply → revert) and the
standardized exports."""

import io
from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import Group, User

from audit.models import AuditLogEntry
from explorer.models import Article, CandidateLink, Source
from review.models import ExportDefinition, ImportBatch

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


@pytest.fixture
def editor(client):
    user = User.objects.create_user("editor", email="editor@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="editor"))
    client.force_login(user)
    return user


@pytest.fixture
def viewer(client):
    user = User.objects.create_user("viewer", email="viewer@localnewsimpact.org")
    user.groups.add(Group.objects.get(name="viewer"))
    client.force_login(user)
    return user


@pytest.fixture
def corpus(crawler_schema):
    source = Source.objects.create(id="s1", host="t.example", host_norm="t.example")
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=source)
    Article.objects.create(
        id="a1",
        candidate_link=link,
        title="Meet the councilâ€™s new chair",
        author="jane doe",
        status="labeled",
        wire_check_status="complete",
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        publish_date=datetime(2026, 3, 1, tzinfo=UTC),
        content="Line one.\nLine two.",
    )
    Article.objects.create(
        id="a2",
        candidate_link=link,
        title="Fine title",
        author="John Smith",
        status="labeled",
        wire_check_status="complete",
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
    )


def _upload(client, text, name="patch.csv"):
    return client.post(
        "/review/import/", {"file": io.BytesIO(text.encode("utf-8-sig"))}
    )


MARCH_CSV = (
    "article_id,title,author\n"
    "a1,Meet the council’s new chair,Jane Doe\n"
    "a2,Fine title,John Smith\n"
    "gone,Ghost row,Nobody\n"
)


def _run_to_diff(client):
    _upload(client, MARCH_CSV)
    batch = ImportBatch.objects.get()
    client.post(
        f"/review/import/{batch.pk}/map/",
        {"key_column": "article_id", "map_title": "title", "map_author": "author"},
    )
    return batch


# --- the protocol -----------------------------------------------------------


def test_upload_parses_and_guesses_the_key(client, editor, corpus):
    response = _upload(client, MARCH_CSV)
    assert response.status_code == 302
    batch = ImportBatch.objects.get()
    assert batch.columns == ["article_id", "title", "author"]
    assert batch.key_column == "article_id"
    assert len(batch.rows) == 3
    assert batch.status == ImportBatch.UPLOADED


def test_non_utf8_is_refused_with_a_reason(client, editor, corpus):
    response = client.post(
        "/review/import/", {"file": io.BytesIO("hé".encode("latin-1"))}
    )
    assert response.status_code == 400
    assert "Not UTF-8" in response.content.decode()
    assert ImportBatch.objects.count() == 0


def test_diff_classifies_before_anything_is_written(client, editor, corpus):
    batch = _run_to_diff(client)
    response = client.get(f"/review/import/{batch.pk}/")
    content = response.content.decode()
    assert "mojibake_fix" in content  # a1 title: exactly the ftfy repair
    assert "edit" in content  # a1 author: jane doe → Jane Doe
    assert "no article with this UUID" in content  # the ghost row
    # Nothing written yet.
    assert Article.objects.get(id="a1").title == "Meet the councilâ€™s new chair"
    assert AuditLogEntry.objects.count() == 0


def test_apply_is_explicit_audited_and_batch_scoped(client, editor, corpus):
    batch = _run_to_diff(client)
    response = client.post(f"/review/import/{batch.pk}/")
    assert response.status_code == 302
    a1 = Article.objects.get(id="a1")
    assert a1.title == "Meet the council’s new chair"
    assert a1.author == "Jane Doe"
    # a2 matched already: untouched.
    assert Article.objects.get(id="a2").author == "John Smith"
    batch.refresh_from_db()
    assert batch.status == ImportBatch.APPLIED
    entry = batch.audit_entry
    assert entry.action == "import:apply"
    assert entry.target_ids == ["a1"]
    assert entry.before["a1"]["title"] == "Meet the councilâ€™s new chair"
    # Applying twice is refused.
    assert client.post(f"/review/import/{batch.pk}/").status_code == 400


def test_batch_revert_restores_and_keeps_history(client, editor, corpus):
    batch = _run_to_diff(client)
    client.post(f"/review/import/{batch.pk}/")
    response = client.post(f"/review/import/{batch.pk}/revert/")
    assert response.status_code == 302
    a1 = Article.objects.get(id="a1")
    assert a1.title == "Meet the councilâ€™s new chair"
    assert a1.author == "jane doe"
    batch.refresh_from_db()
    assert batch.status == ImportBatch.REVERTED
    assert AuditLogEntry.objects.count() == 2


def test_mapping_refuses_fields_outside_the_boundary(client, editor, corpus):
    _upload(client, "article_id,url\na1,https://evil/\n")
    batch = ImportBatch.objects.get()
    response = client.post(
        f"/review/import/{batch.pk}/map/",
        {"key_column": "article_id", "map_url": "url"},
    )
    assert response.status_code == 400


def test_viewer_cannot_import(client, viewer, corpus):
    assert client.get("/review/import/").status_code == 403


# --- export -----------------------------------------------------------------


def test_export_is_bom_utf8_one_line_per_row(client, viewer, corpus):
    response = client.post(
        "/review/export/",
        {"columns": ["title", "author", "content"], "f_status": "labeled"},
    )
    raw = response.content
    assert raw.startswith("﻿".encode())
    body = raw.decode("utf-8-sig")
    lines = body.strip().split("\n")
    assert lines[0] == "article_uuid,title,author,content"
    assert len(lines) == 3  # header + two articles, embedded newline flattened
    assert "Line one.\\nLine two." in body


def test_export_respects_grid_filters(client, viewer, corpus):
    response = client.post("/review/export/", {"columns": ["title"], "f_q": "Fine"})
    body = response.content.decode("utf-8-sig")
    assert "a2" in body
    assert "a1" not in body


def test_saved_definition_reruns_against_current_data(client, viewer, corpus):
    client.post(
        "/review/export/",
        {"columns": ["title"], "f_status": "labeled", "save_as": "march-titles"},
    )
    definition = ExportDefinition.objects.get(name="march-titles")
    Article.objects.filter(id="a2").update(status="out_of_scope")
    response = client.get(f"/review/export/{definition.pk}/run/")
    body = response.content.decode("utf-8-sig")
    assert "a1" in body
    assert "a2" not in body


def test_export_requires_sign_in(client, corpus):
    assert client.get("/review/export/").status_code == 302
