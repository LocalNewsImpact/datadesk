"""Migrations apply cleanly, and the role groups are gone."""

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command

from accounts.models import DATADESK, Grant


@pytest.mark.django_db
def test_migrations_run_clean():
    # The test database is built by running every migration; this command
    # confirms the plan is fully applied with nothing outstanding.
    call_command("migrate", verbosity=0, interactive=False)


@pytest.mark.django_db
def test_the_role_groups_are_gone_after_migrate():
    """0001 created viewer, editor and admin as Django groups; 0005
    retires them. A group left behind would be a second place a role
    could live, which is what grants exist to end."""
    names = set(Group.objects.values_list("name", flat=True))
    assert not ({"viewer", "editor", "admin"} & names)


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
def test_a_superuser_holds_everything_without_a_grant(django_user_model):
    """The account flag carries global admin, so a superuser needs no
    row -- and the retirement migration skips them for that reason."""
    from accounts.access import has_privilege, is_application_admin
    from accounts.privileges import CREATE, DESIGN, READ, WRITE

    su = django_user_model.objects.create_superuser("root", email="root@example.org")
    for privilege in (READ, WRITE, CREATE, DESIGN):
        assert has_privilege(su, DATADESK, privilege, scope="anything")
    assert is_application_admin(su, DATADESK)
    assert Grant.objects.filter(user=su).count() == 0


@pytest.mark.django_db
def test_the_retirement_migration_carries_a_role_across(django_user_model):
    """0005 has already run by the time this test does, so it recreates
    the situation and runs the function again.

    The rule that matters: somebody in a role group gets the
    application-wide grant that group meant. The groups had no scope --
    "editor" meant every dataset -- which is what an empty scope says
    now.
    """
    import importlib

    from django.apps import apps as registry

    # importlib, because a module name starting with a digit cannot be
    # written in an import statement.
    migration = importlib.import_module("accounts.migrations.0005_retire_role_groups")

    editor = Group.objects.create(name="editor")
    person = django_user_model.objects.create_user("e", email="e@example.org")
    person.groups.add(editor)

    migration.groups_to_grants(registry, None)

    grant = Grant.objects.get(user=person)
    assert (grant.app, grant.scope, grant.role) == (DATADESK, "", "editor")
    assert not Group.objects.filter(name="editor").exists()


@pytest.mark.django_db
def test_a_superuser_gets_no_grant_from_the_retirement(django_user_model):
    """They hold everything from the account flag, so a row would be
    noise -- and in this database both accounts are superusers, which is
    why the migration moved nothing here."""
    from accounts.access import is_application_admin

    su = django_user_model.objects.create_superuser("su", email="su@example.org")
    assert is_application_admin(su, DATADESK)
    assert not Grant.objects.filter(user=su).exists()
