"""The audit log is append-only, at the model and at the admin."""

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from audit.admin import AuditLogEntryAdmin
from audit.models import AppendOnlyError, AuditLogEntry


@pytest.fixture
def actor(django_user_model):
    return django_user_model.objects.create_user("editor1", email="e1@example.org")


@pytest.fixture
def entry(actor):
    return AuditLogEntry.objects.create(
        actor=actor,
        action="article.byline_edit",
        target_table="articles",
        target_ids=["a-uuid"],
        before={"author": "Staf Writer"},
        after={"author": "Staff Writer"},
    )


@pytest.mark.django_db
def test_entries_can_be_created(entry):
    assert AuditLogEntry.objects.count() == 1
    assert entry.timestamp is not None


@pytest.mark.django_db
def test_update_raises(entry):
    entry.action = "rewritten"
    with pytest.raises(AppendOnlyError):
        entry.save()


@pytest.mark.django_db
def test_delete_raises(entry):
    with pytest.raises(AppendOnlyError):
        entry.delete()
    assert AuditLogEntry.objects.count() == 1


@pytest.mark.django_db
def test_admin_is_read_only(django_user_model, entry):
    admin_user = django_user_model.objects.create_superuser(
        "root", email="root@example.org"
    )
    request = RequestFactory().get("/admin/audit/auditlogentry/")
    request.user = admin_user
    model_admin = AuditLogEntryAdmin(AuditLogEntry, AdminSite())

    assert model_admin.has_view_permission(request, entry) is True
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request, entry) is False
    assert model_admin.has_delete_permission(request, entry) is False
