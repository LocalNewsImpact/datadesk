"""The audited write path: boundary, edits with ftfy preview, bulk
dispositions, and revert."""

from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import Group, User

from audit.models import AuditLogEntry
from explorer.models import Article, ArticleEnrichment, CandidateLink, Source
from review.services import BoundaryViolation, audited_update, revert

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
def article(crawler_schema):
    source = Source.objects.create(id="s1", host="t.example", host_norm="t.example")
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=source)
    return Article.objects.create(
        id="a1",
        candidate_link=link,
        title="Meet the councilâ€™s new chair",
        author="Jane Doe",
        content="The councilâ€™s vote was unanimous.",
        status="labeled",
        wire_check_status="complete",
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
    )


# --- the service ------------------------------------------------------------


def test_audited_update_applies_and_records(editor, article):
    entry = audited_update(
        editor, [article], {"author": "Jane A. Doe"}, "edit:author", reason="byline fix"
    )
    article.refresh_from_db()
    assert article.author == "Jane A. Doe"
    assert entry.target_table == "articles"
    assert entry.target_ids == ["a1"]
    assert entry.before == {"a1": {"author": "Jane Doe"}}
    assert entry.after == {"author": "Jane A. Doe"}
    assert entry.reason == "byline fix"


def test_boundary_refuses_fields_outside_the_grants(editor, article):
    with pytest.raises(BoundaryViolation):
        audited_update(editor, [article], {"url": "https://x/"}, "edit:url")
    with pytest.raises(BoundaryViolation):
        audited_update(
            editor,
            [article.candidate_link],
            {"url": "https://x/"},
            "edit:candidate",
        )


def test_revert_restores_before_values(editor, article):
    entry = audited_update(editor, [article], {"author": "Wrong"}, "edit:author")
    compensating = revert(editor, entry)
    article.refresh_from_db()
    assert article.author == "Jane Doe"
    assert compensating.action == "revert:edit:author"
    assert compensating.after == {"a1": {"author": "Jane Doe"}}
    # History intact: both entries present.
    assert AuditLogEntry.objects.count() == 2


# --- inline edit with ftfy preview ------------------------------------------


def test_edit_form_shows_mojibake_repair(client, editor, article):
    response = client.get("/review/articles/a1/edit/title/")
    content = response.content.decode()
    assert "Mojibake detected" in content
    assert "Meet the council’s new chair" in content  # the repaired form


def test_apply_repaired_version(client, editor, article):
    response = client.post(
        "/review/articles/a1/edit/content/", {"use_repaired": "1", "reason": "ftfy"}
    )
    assert response.status_code == 302
    article.refresh_from_db()
    assert article.content == "The council’s vote was unanimous."
    entry = AuditLogEntry.objects.get()
    assert entry.action == "edit:content"
    assert entry.before["a1"]["content"] == "The councilâ€™s vote was unanimous."


def test_manual_edit(client, editor, article):
    client.post(
        "/review/articles/a1/edit/author/",
        {"value": "Jane A. Doe", "reason": "add middle initial"},
    )
    article.refresh_from_db()
    assert article.author == "Jane A. Doe"


def test_clean_field_offers_no_repair(client, editor, article):
    response = client.get("/review/articles/a1/edit/author/")
    assert "Mojibake detected" not in response.content.decode()


def test_viewer_cannot_edit(client, viewer, article):
    assert client.get("/review/articles/a1/edit/title/").status_code == 403
    client.post("/review/articles/a1/edit/title/", {"value": "X"})
    article.refresh_from_db()
    assert article.title == "Meet the councilâ€™s new chair"


def test_only_text_fields_are_editable_inline(client, editor, article):
    assert client.get("/review/articles/a1/edit/status/").status_code == 404


# --- bulk dispositions ------------------------------------------------------


@pytest.fixture
def corpus(crawler_schema):
    source = Source.objects.create(id="s1", host="t.example", host_norm="t.example")
    link = CandidateLink.objects.create(id="cl1", url="https://t/", source=source)
    articles = []
    for i in range(3):
        articles.append(
            Article.objects.create(
                id=f"a{i}",
                candidate_link=link,
                title=f"Story {i}",
                status="labeled",
                wire_check_status="complete",
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
        )
    ArticleEnrichment.objects.create(article=articles[0], skip_reason=None)
    return articles


def test_bulk_out_of_scope(client, editor, corpus):
    response = client.post(
        "/review/articles/disposition/",
        {
            "ids": ["a0", "a1"],
            "disposition": "out_of_scope",
            "reason": "city outside dataset footprint",
            "next": "/explorer/articles/?status=labeled",
        },
    )
    assert response.status_code == 302
    assert response["Location"] == "/explorer/articles/?status=labeled"
    assert set(
        Article.objects.filter(status="out_of_scope").values_list("id", flat=True)
    ) == {"a0", "a1"}
    assert Article.objects.get(id="a2").status == "labeled"
    # The enrichment row that exists got the skip_reason; entries recorded.
    assert (
        ArticleEnrichment.objects.get(article_id="a0").skip_reason
        == "city outside dataset footprint"
    )
    actions = set(AuditLogEntry.objects.values_list("action", flat=True))
    assert actions == {"disposition:out_of_scope", "disposition:skip_reason"}


def test_out_of_scope_requires_a_reason(client, editor, corpus):
    response = client.post(
        "/review/articles/disposition/",
        {"ids": ["a0"], "disposition": "out_of_scope", "reason": "  "},
    )
    assert response.status_code == 400
    assert Article.objects.get(id="a0").status == "labeled"


def test_wire_override(client, editor, corpus):
    client.post(
        "/review/articles/disposition/",
        {
            "ids": ["a2"],
            "disposition": "wire",
            "wire_status": "local",
            "reason": "manually verified local",
        },
    )
    assert Article.objects.get(id="a2").wire_check_status == "local"


def test_viewer_cannot_disposition(client, viewer, corpus):
    response = client.post(
        "/review/articles/disposition/",
        {"ids": ["a0"], "disposition": "out_of_scope", "reason": "x"},
    )
    assert response.status_code == 403


def test_open_redirect_is_refused(client, editor, corpus):
    response = client.post(
        "/review/articles/disposition/",
        {
            "ids": ["a0"],
            "disposition": "wire",
            "wire_status": "local",
            "next": "https://evil.example/",
        },
    )
    assert response["Location"] == "/explorer/articles/"


# --- the audit page and revert endpoint -------------------------------------


def test_audit_page_lists_and_reverts(client, editor, article):
    audited_update(editor, [article], {"author": "Wrong"}, "edit:author")
    entry = AuditLogEntry.objects.get()
    page = client.get("/review/audit/")
    assert "edit:author" in page.content.decode()
    response = client.post(f"/review/audit/{entry.pk}/revert/")
    assert response.status_code == 302
    article.refresh_from_db()
    assert article.author == "Jane Doe"


def test_bulk_disposition_is_revertible(client, editor, corpus):
    client.post(
        "/review/articles/disposition/",
        {"ids": ["a0", "a1"], "disposition": "out_of_scope", "reason": "mistake"},
    )
    entry = AuditLogEntry.objects.get(action="disposition:out_of_scope")
    client.post(f"/review/audit/{entry.pk}/revert/")
    assert Article.objects.get(id="a0").status == "labeled"
    assert Article.objects.get(id="a1").status == "labeled"
