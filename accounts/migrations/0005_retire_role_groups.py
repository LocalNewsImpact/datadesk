"""Retire the three role groups (ROADMAP item 1).

`0001_create_role_groups` made viewer, editor and admin as Django groups,
and membership of one was what a role meant. Grants replaced that: a role
is held per application and per dataset, and one person holds several.

Anyone still in a group gets the equivalent application-wide grant, which
is exactly what the group meant — the groups had no scope, so "editor"
meant every dataset. Superusers are skipped: they hold everything from
the account flag and a grant would be noise.

In this database both accounts are superusers and one is in the admin
group, so this moves nothing. It is written for the general case anyway,
because a migration that only works on the data in front of it is a
migration nobody can replay.

Reversible. Going back re-creates the groups and restores membership from
the application-wide grants, so a rollback does not silently drop
somebody's access.
"""

from django.db import migrations

ROLE_NAMES = ("viewer", "editor", "admin")
DATADESK = "datadesk"
WHOLE_APPLICATION = ""


def groups_to_grants(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Grant = apps.get_model("accounts", "Grant")

    for group in Group.objects.filter(name__in=ROLE_NAMES):
        for user in group.user_set.all():
            if user.is_superuser:
                continue
            # get_or_create rather than create: the unique constraint is
            # on (user, app, scope), so somebody already granted through
            # the admin screen keeps what they have rather than colliding.
            Grant.objects.get_or_create(
                user=user,
                app=DATADESK,
                scope=WHOLE_APPLICATION,
                defaults={"role": group.name},
            )

    Group.objects.filter(name__in=ROLE_NAMES).delete()


def grants_to_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Grant = apps.get_model("accounts", "Grant")

    groups = {name: Group.objects.get_or_create(name=name)[0] for name in ROLE_NAMES}
    for grant in Grant.objects.filter(app=DATADESK, scope=WHOLE_APPLICATION):
        group = groups.get(grant.role)
        if group is not None:
            group.user_set.add(grant.user)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_role_labels"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(groups_to_grants, grants_to_groups),
    ]
