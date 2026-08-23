"""Users and Roles: the admin's view of who can do what."""

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry

pytestmark = pytest.mark.django_db


def _with_role(username, role, **kwargs):
    user = User.objects.create_user(
        username, email=f"{username}@localnewsimpact.org", **kwargs
    )
    if role:
        Grant.objects.create(user=user, app=DATADESK, scope="", role=role)
    return user


@pytest.fixture
def admin(client):
    user = _with_role("boss", "admin")
    client.force_login(user)
    return user


def test_users_page_lists_accounts_with_role_and_last_sign_in(client, admin):
    _with_role("reader", "viewer")
    content = client.get("/manage/users/").content.decode()
    assert "reader@localnewsimpact.org" in content
    assert "viewer" in content
    assert "never" in content  # last sign-in


def test_roles_page_offers_every_role(client, admin):
    _with_role("reader", "viewer")
    content = client.get("/manage/roles/").content.decode()
    for role in ("viewer", "editor", "admin"):
        assert f'value="{role}"' in content


def test_assigning_a_role_moves_the_user_and_is_audited(client, admin):
    target = _with_role("reader", "viewer")
    response = client.post(
        "/manage/roles/set/",
        {"user_id": target.pk, "role": "editor", "reason": "runs the backpatch"},
    )
    assert response.status_code == 302
    assert set(target.grants.values_list("role", flat=True)) == {"editor"}

    entry = AuditLogEntry.objects.get(action="role_change")
    assert entry.actor == admin
    assert entry.target_ids == [str(target.pk)]
    assert entry.before == {"role": "viewer"}
    assert entry.after == {"role": "editor"}
    assert entry.reason == "runs the backpatch"


def test_a_role_can_be_removed_entirely(client, admin):
    target = _with_role("reader", "viewer")
    client.post("/manage/roles/set/", {"user_id": target.pk, "role": ""})
    assert list(target.grants.all()) == []
    assert AuditLogEntry.objects.get(action="role_change").after == {"role": None}


def test_an_admin_cannot_remove_their_own_admin_role(client, admin):
    """The classic failure is locking the last admin out. Refusing
    self-demotion prevents it: whoever makes a change keeps their own
    role, so an admin always remains."""
    response = client.post(
        "/manage/roles/set/", {"user_id": admin.pk, "role": "viewer"}
    )
    assert response.status_code == 302
    admin.refresh_from_db()
    assert set(admin.grants.values_list("role", flat=True)) == {"admin"}
    assert not AuditLogEntry.objects.filter(action="role_change").exists()


def test_an_admin_may_demote_another_admin(client, admin):
    other = _with_role("deputy", "admin")
    client.post("/manage/roles/set/", {"user_id": other.pk, "role": "editor"})
    assert set(other.grants.values_list("role", flat=True)) == {"editor"}
    # The console still has an admin: the one who made the change.
    assert Grant.objects.filter(app=DATADESK, role="admin").count() == 1


def test_a_superusers_role_is_not_changed_here(client, admin):
    root = User.objects.create_superuser("root", "root@localnewsimpact.org", "x")
    client.post("/manage/roles/set/", {"user_id": root.pk, "role": "viewer"})
    assert list(root.grants.all()) == []
    assert not AuditLogEntry.objects.filter(action="role_change").exists()


def test_an_invented_role_is_refused(client, admin):
    target = _with_role("reader", "viewer")
    client.post("/manage/roles/set/", {"user_id": target.pk, "role": "superadmin"})
    assert set(target.grants.values_list("role", flat=True)) == {"viewer"}
    assert not AuditLogEntry.objects.filter(action="role_change").exists()


def test_an_unknown_account_is_refused(client, admin):
    client.post("/manage/roles/set/", {"user_id": "99999", "role": "viewer"})
    assert not AuditLogEntry.objects.filter(action="role_change").exists()


def test_setting_the_role_a_user_already_has_records_nothing(client, admin):
    target = _with_role("reader", "viewer")
    client.post("/manage/roles/set/", {"user_id": target.pk, "role": "viewer"})
    assert not AuditLogEntry.objects.filter(action="role_change").exists()


def test_role_assignment_is_post_only(client, admin):
    assert client.get("/manage/roles/set/").status_code == 405
