"""Let a published visual actually be embedded.

`frame_ancestors` defaulted to `'self'`, so the only site allowed to frame
an embed was the one serving it. Every snippet pasted into somebody else's
article showed a browser security refusal instead of a chart.

Rows still carrying the old default are moved to `*`, because that default
was never a decision anybody made about a particular visual -- it was the
field's initial value. A row an author narrowed by hand says something
different and is left alone.
"""

from django.db import migrations, models

OLD_DEFAULT = "'self'"
NEW_DEFAULT = "*"


def widen_untouched_rows(apps, schema_editor):
    Visual = apps.get_model("visuals", "Visual")
    Visual.objects.filter(frame_ancestors=OLD_DEFAULT).update(
        frame_ancestors=NEW_DEFAULT
    )


def narrow_untouched_rows(apps, schema_editor):
    Visual = apps.get_model("visuals", "Visual")
    Visual.objects.filter(frame_ancestors=NEW_DEFAULT).update(
        frame_ancestors=OLD_DEFAULT
    )


class Migration(migrations.Migration):
    dependencies = [("visuals", "0004_visual_datasets")]

    operations = [
        migrations.AlterField(
            model_name="visual",
            name="frame_ancestors",
            field=models.CharField(
                default="*",
                help_text=(
                    "CSP frame-ancestors sources, space-separated. "
                    "`*` lets any site embed this; name hosts to restrict it."
                ),
                max_length=500,
            ),
        ),
        migrations.RunPython(widen_untouched_rows, narrow_untouched_rows),
    ]
