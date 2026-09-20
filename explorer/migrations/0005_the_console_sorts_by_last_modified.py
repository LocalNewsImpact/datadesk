"""An article says when it last changed.

State only: the model is unmanaged. The crawler's migration c2d3e4f5a6b8
added `articles.last_modified` with a `BEFORE INSERT OR UPDATE` trigger
and an index on `last_modified DESC`. This records the same shape so
`makemigrations --check` agrees with the model, and the test schema in
tests/conftest.py mirrors production.

Nothing here writes the column. The trigger owns it, which is what makes
it trustworthy for writes this console makes through `datadesk_rw` --
a status change or a headline repair moves no other timestamp.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("explorer", "0004_raw_is_the_capture")]

    operations = [
        migrations.AddField(
            model_name="article",
            name="last_modified",
            field=models.DateTimeField(null=True),
        ),
    ]
