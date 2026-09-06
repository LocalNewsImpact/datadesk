"""The entry a revert undoes, recorded as a link rather than as prose.

`revert()` wrote "revert of audit entry 12" into the compensating
entry's reason, and a reviewer supplying their own reason replaced it.
Whether an entry had already been reverted was therefore a question
about the wording of free text, which is not a question a page can
answer.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("audit", "0002_reason")]

    operations = [
        migrations.AddField(
            model_name="auditlogentry",
            name="reverts",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="reverted_by",
                to="audit.auditlogentry",
            ),
        ),
    ]
