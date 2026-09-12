"""An account for the work nobody does by hand.

`reconcile_queues --apply` writes through the audited path, which records
who made each change, and `--actor` resolves an email to a `User`. On a
schedule there is no person to name: the job passes its service account,
`datadesk-run@lnic-datadesk.iam.gserviceaccount.com`, and no user had
that address -- so every firing would have exited with "no user with
email ..." and written nothing.

That is the right failure rather than the wrong one: an audited write
with no writer records a change nobody made. But it would have failed
every night, and the schedule is the only thing that keeps the records
true.

So the account exists, and it is honest about what it is:

  - `is_active=False`, so it cannot authenticate. It is a name in an
    audit trail, not a login, and nothing about creating it opens a door.
  - `is_staff=False`, `is_superuser=False`. The write boundary
    (`review.services.WRITABLE`) is what permits the changes; the actor
    is who to blame for them, and those are separate questions.
  - named for the job rather than the machine, so "who retracted these
    161 articles" answers with something a person can act on.
"""

from django.db import migrations

ACTOR_EMAIL = "datadesk-run@lnic-datadesk.iam.gserviceaccount.com"
ACTOR_USERNAME = "nightly-reconciliation"


def add_actor(apps, schema_editor):
    User = apps.get_model("auth", "User")
    User.objects.update_or_create(
        username=ACTOR_USERNAME,
        defaults={
            "email": ACTOR_EMAIL,
            "first_name": "Nightly",
            "last_name": "reconciliation",
            # Unusable: `set_unusable_password` is not available on a
            # historical model, and "!" is the prefix Django itself uses
            # for exactly this -- no password hash can start with it, so
            # no input can ever match.
            "password": "!",
            "is_active": False,
            "is_staff": False,
            "is_superuser": False,
        },
    )


def remove_actor(apps, schema_editor):
    # The audit entries it wrote survive: `AuditLogEntry.actor` is what
    # answers "who changed this", and deleting the row it points at
    # answers "nobody". Deactivating is the whole of a reversal.
    User = apps.get_model("auth", "User")
    User.objects.filter(username=ACTOR_USERNAME).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("review", "0019_codebooksettings"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(add_actor, remove_actor)]
