"""The words the corpus already uses, as rows somebody can add to.

Seeded rather than left empty. An empty vocabulary means "every recorded
value is wrong", which on the morning of the migration is 1,149 records
and no way to tell which of them a person should actually look at.

Reversible by emptying the table: the code falls back to the same tuples
these rows were made from, so going backwards is the state before, not a
console with no vocabulary at all.
"""

from django.db import migrations


def seed(apps, schema_editor):
    from datasets.publishers import GROUPED_VALUES

    Term = apps.get_model("datasets", "VocabularyTerm")
    for vocabulary, groups in GROUPED_VALUES.items():
        for _key, label, spelling, covered in groups:
            for folded in covered:
                Term.objects.get_or_create(
                    vocabulary=vocabulary,
                    value=folded,
                    defaults={"label": label, "spelling": spelling},
                )


def unseed(apps, schema_editor):
    Term = apps.get_model("datasets", "VocabularyTerm")
    Term.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("datasets", "0002_vocabularyterm")]
    operations = [migrations.RunPython(seed, unseed)]
