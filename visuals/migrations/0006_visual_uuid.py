"""Give every visual a name that cannot change under its readers.

Three operations, and the details of all three matter.

`AddField` evaluates a callable default **once** and writes that single
value to every existing row. So the column is added with no default at all
-- every existing row gets NULL -- and the values are filled in afterwards,
one call per row. Adding it with `default=uuid.uuid4` instead gives five
visuals the same uuid and the unique index then refuses to build:

    IntegrityError: could not create unique index
    DETAIL: Key (uuid)=(3c5ecdac-...) is duplicated.

which is what this migration did before it was run against a table that
already had rows in it. An empty test database never shows it.

The default belongs on the final field, where it applies to rows inserted
from here on, one at a time, by the ORM.
"""

import uuid

from django.db import migrations, models


def give_each_one_its_own(apps, schema_editor):
    """Every row, not just the null ones. Filtering on NULL would be
    correct given the AddField above and silently wrong if it ever gained
    a default again -- the rows would all be non-null, all identical, and
    all skipped."""
    Visual = apps.get_model("visuals", "Visual")
    for visual in Visual.objects.all().iterator():
        visual.uuid = uuid.uuid4()
        visual.save(update_fields=["uuid"])


def drop_them(apps, schema_editor):
    """Reversing this discards the identifiers, and any URL built from one
    stops resolving. That is what reversing it means; it is written down
    here rather than left as a silent loss."""
    apps.get_model("visuals", "Visual").objects.update(uuid=None)


class Migration(migrations.Migration):
    dependencies = [("visuals", "0005_frame_ancestors_permissive")]

    operations = [
        migrations.AddField(
            model_name="visual",
            name="uuid",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(give_each_one_its_own, drop_them),
        migrations.AlterField(
            model_name="visual",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
    ]
