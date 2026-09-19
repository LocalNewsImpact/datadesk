"""`raw` is the capture, `text` is the clean.

State only: the model is unmanaged. The crawler's migration b1c2d3e4f5a7
renamed `articles.content` to `raw` and re-pointed `text_length` at the
cleaned body. This records the same shape so `makemigrations --check`
agrees with the model and the test schema in tests/conftest.py mirrors
production.
"""

from django.db import migrations, models
from django.db.models.functions import Coalesce, Length


class Migration(migrations.Migration):
    dependencies = [("explorer", "0003_pipelinerework")]

    operations = [
        migrations.RenameField(
            model_name="article", old_name="content", new_name="raw"
        ),
        migrations.AlterField(
            model_name="article",
            name="text_length",
            field=models.GeneratedField(
                db_persist=True,
                expression=Length(
                    Coalesce(
                        "text",
                        "raw",
                        "text_excerpt",
                        models.Value(""),
                        output_field=models.TextField(),
                    )
                ),
                output_field=models.IntegerField(),
            ),
        ),
    ]
