"""The canonical spellings are words, separated by spaces.

Seeded from whichever spelling the most records happened to carry, they
came out inconsistent with each other -- `digital native` beside
`video_broadcast` -- so the queue proposed an underscore for one kind and
a space for another, and read as though the underscore were the correct
form of that word.

Only rows still holding the seeded value are changed. A spelling somebody
has since edited on the schema page is their decision and stays.
"""

from django.db import migrations

#: (vocabulary, old spelling, new spelling)
CHANGED = (
    ("publisher_type", "video_broadcast", "video broadcast"),
    ("publisher_type", "audio_broadcast", "audio broadcast"),
)


def to_words(apps, schema_editor):
    Term = apps.get_model("datasets", "VocabularyTerm")
    for vocabulary, was, now in CHANGED:
        Term.objects.filter(vocabulary=vocabulary, spelling=was).update(spelling=now)


def to_underscores(apps, schema_editor):
    Term = apps.get_model("datasets", "VocabularyTerm")
    for vocabulary, was, now in CHANGED:
        Term.objects.filter(vocabulary=vocabulary, spelling=now).update(spelling=was)


class Migration(migrations.Migration):
    dependencies = [("datasets", "0003_seed_vocabulary")]
    operations = [migrations.RunPython(to_words, to_underscores)]
