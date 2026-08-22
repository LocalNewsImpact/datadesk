"""A queue item is a record with a named defect, not an imported proposal.

`finding` described the state of a proposed edit ("passes every check");
`flag` names what is wrong with the record (REVIEW.md). The values do
not map onto each other, so the column is replaced rather than renamed,
and the queue is refilled by scanning the corpus.
"""

from django.db import migrations, models


def drop_old_pending(apps, schema_editor):
    # Pending rows were built under the old meaning and have no flag.
    # Decisions are kept: they record what a person chose.
    ChangeProposal = apps.get_model("review", "ChangeProposal")
    ChangeProposal.objects.filter(state="pending").delete()


class Migration(migrations.Migration):
    dependencies = [("review", "0005_alter_changeproposal_finding")]

    operations = [
        migrations.RunPython(drop_old_pending, migrations.RunPython.noop),
        migrations.RemoveField(model_name="changeproposal", name="finding"),
        migrations.RenameField(
            model_name="changeproposal", old_name="why", new_name="detail"
        ),
        migrations.AddField(
            model_name="changeproposal",
            name="flag",
            field=models.CharField(default="", max_length=40),
        ),
    ]
