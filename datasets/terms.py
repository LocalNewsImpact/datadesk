"""Reading a vocabulary, wherever it is kept.

Two readers and one answer: the builder folds a spelling so one kind is
one filter, and the review queue raises that same spelling as a record to
fix. They already shared `datasets/publishers.py`; what changes here is
that the list can grow without a deploy.

The rows are the vocabulary. The tuples in `publishers.py` are what the
corpus already used the day this was written, and they are the seed and
the fallback -- with no rows at all, reading a vocabulary answers from
them rather than declaring every recorded value wrong, which is what an
empty table would otherwise mean on the morning of the migration.
"""

from django.core.cache import cache

from datasets.publishers import GROUPED_VALUES, fold_value

#: Long enough that a page of records does not ask per row, short enough
#: that somebody who adds a word sees it take effect while they are still
#: looking at the page.
_HELD_SECONDS = 60


def _seeded(vocabulary):
    """The vocabulary as the corpus already used it: {folded: (spelling, label)}."""
    out = {}
    for _key, label, spelling, covered in GROUPED_VALUES.get(vocabulary, ()):
        for folded in covered:
            out[folded] = (spelling, label)
    return out


def terms(vocabulary):
    """{folded value: (spelling to use, label)} for one vocabulary."""
    key = f"datasets.terms.{vocabulary}"
    held = cache.get(key)
    if held is not None:
        return held

    from datasets.models import VocabularyTerm

    rows = VocabularyTerm.objects.filter(vocabulary=vocabulary, retired=False)
    # The spelling is not defaulted to the value. Empty means the
    # vocabulary has no one spelling to offer -- bi-weekly, tri-weekly and
    # semi-weekly are one group and three different answers -- and falling
    # back to the value would propose the folded form of a word as a fix
    # for the word itself.
    found = {
        fold_value(row.value): (row.spelling, row.label or row.value) for row in rows
    }
    # The seed answers only where nothing has been maintained. A vocabulary
    # somebody has edited is the whole answer, or retiring a word would
    # leave it still accepted by the list it came from.
    value = found or _seeded(vocabulary)
    cache.set(key, value, _HELD_SECONDS)
    return value


def forget(vocabulary=""):
    """Drop what is held, so an edit is visible on the next read."""
    names = [vocabulary] if vocabulary else list(GROUPED_VALUES)
    for name in names:
        cache.delete(f"datasets.terms.{name}")


def known(vocabulary, value):
    """Is this a word the vocabulary has?"""
    return fold_value(value) in terms(vocabulary)


def spelling_for(vocabulary, value):
    """The spelling this vocabulary uses, or "" where it has none to offer
    or the value is already written that way."""
    found = terms(vocabulary).get(fold_value(value))
    if not found:
        return ""
    spelling = found[0]
    return spelling if spelling and spelling != str(value).strip() else ""
