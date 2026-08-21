"""Migrations apply cleanly on sqlite and create the role groups."""

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command

from accounts import roles


@pytest.mark.django_db
def test_migrations_run_clean():
    # The test database is built by running every migration; this command
    # confirms the plan is fully applied with nothing outstanding.
    call_command("migrate", verbosity=0, interactive=False)


@pytest.mark.django_db
def test_role_groups_exist_after_migrate():
    names = set(Group.objects.values_list("name", flat=True))
    assert {"viewer", "editor", "admin"} <= names


@pytest.mark.django_db
def test_role_precedence(django_user_model):
    user = django_user_model.objects.create_user("u1", email="u1@example.org")
    assert roles.role_for_user(user) is None
    user.groups.add(Group.objects.get(name="viewer"))
    assert roles.role_for_user(user) == "viewer"
    user.groups.add(Group.objects.get(name="editor"))
    assert roles.role_for_user(user) == "editor"
    user.groups.add(Group.objects.get(name="admin"))
    assert roles.role_for_user(user) == "admin"


@pytest.mark.django_db
def test_superuser_reports_admin(django_user_model):
    su = django_user_model.objects.create_superuser("root", email="root@example.org")
    assert roles.role_for_user(su) == "admin"
