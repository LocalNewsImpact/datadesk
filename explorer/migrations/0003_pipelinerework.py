"""`PipelineRework` in migration state, and no table.

The model is `managed = False`: the crawler owns `pipeline_rework` and
creates it in alembic revision x9y0z1a2b3c4. Django records unmanaged
models in migration state, and `makemigrations --check` fails the build
until it does.

Nothing here runs against the database.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("explorer", "0002_articleplacemanual"),
    ]

    operations = [
        migrations.CreateModel(
            name="PipelineRework",
            fields=[
                ("id", models.AutoField(primary_key=True, serialize=False)),
                ("record_type", models.TextField()),
                ("record_id", models.TextField()),
                ("stage", models.TextField()),
                ("reason", models.TextField(null=True)),
                ("requested_by", models.TextField()),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("done_at", models.DateTimeField(null=True)),
                ("outcome", models.TextField(null=True)),
            ],
            options={
                "db_table": "pipeline_rework",
                "abstract": False,
                "managed": False,
            },
        ),
    ]
