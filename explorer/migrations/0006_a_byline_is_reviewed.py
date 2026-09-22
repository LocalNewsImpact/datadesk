"""The byline review tables in migration state, and no tables.

Both models are `managed = False`: the crawler owns `byline_review_candidates`
(alembic 9d3a7e2b1c48) and `byline_normalizations` (7b1e5c9a4d20) and creates
them there. Django records unmanaged models in migration state anyway, and
`makemigrations --check` fails the build until it does.

Nothing here runs against the database.
"""

from django.db import migrations, models

import explorer.models


class Migration(migrations.Migration):

    dependencies = [
        ("explorer", "0005_the_console_sorts_by_last_modified"),
    ]

    operations = [
        migrations.CreateModel(
            name="BylineNormalization",
            fields=[
                ("id", models.TextField(primary_key=True, serialize=False)),
                ("dataset_id", models.TextField()),
                ("raw_byline", models.TextField()),
                ("canonical_names", explorer.models.DecodedJSONField(null=True)),
                ("decision", models.TextField()),
                ("reason", models.TextField(null=True)),
                ("decided_by", models.TextField(null=True)),
                ("decided_at", models.DateTimeField(null=True)),
                ("applied_at", models.DateTimeField(null=True)),
                ("articles_updated", models.IntegerField(null=True)),
            ],
            options={
                "db_table": "byline_normalizations",
                "abstract": False,
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="BylineReviewCandidate",
            fields=[
                ("id", models.TextField(primary_key=True, serialize=False)),
                ("dataset_id", models.TextField()),
                ("raw_byline", models.TextField()),
                ("signal", models.TextField()),
                ("signal_label", models.TextField()),
                ("signals", explorer.models.DecodedJSONField(null=True)),
                ("proposed", explorer.models.DecodedJSONField(null=True)),
                ("variants", explorer.models.DecodedJSONField(null=True)),
                ("differs_by", explorer.models.DecodedJSONField(null=True)),
                ("articles", models.IntegerField(default=0)),
                ("hosts", explorer.models.DecodedJSONField(null=True)),
                ("owners", explorer.models.DecodedJSONField(null=True)),
                ("computed_at", models.DateTimeField(null=True)),
            ],
            options={
                "db_table": "byline_review_candidates",
                "abstract": False,
                "managed": False,
            },
        ),
    ]
