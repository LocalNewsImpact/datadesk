"""Migrations apply cleanly on sqlite and create the role groups."""

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command

from accounts import roles
from accounts.models import DATADESK, Grant


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
def test_there_is_no_precedence_to_apply(django_user_model):
    """The three global groups needed a rule for who wins when somebody
    held two of them. One role per person per scope means the question
    never arises, and the database is what makes that true rather than a
    convention -- so this asserts the constraint, not a ranking.
    """
    from django.db import IntegrityError, transaction

    user = django_user_model.objects.create_user("u1", email="u1@example.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="viewer")
    with pytest.raises(IntegrityError), transaction.atomic():
        Grant.objects.create(user=user, app=DATADESK, scope="", role="editor")


@pytest.mark.django_db
def test_superuser_reports_admin(django_user_model):
    su = django_user_model.objects.create_superuser("root", email="root@example.org")
    assert roles.role_for_user(su) == "admin"
