"""Reasons ride with the action (SCOPE.md §2.2: dispositions with
recorded reasons)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="auditlogentry",
            name="reason",
            field=models.TextField(blank=True, default=""),
        ),
    ]
