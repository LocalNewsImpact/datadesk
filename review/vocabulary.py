"""The words an extraction review uses, and where they can be changed.

The publisher schema page already draws this line: which fields a record
needs is a decision somebody reviews in a change; the words a field
accepts are a list that grows on a Tuesday, and making somebody ship a
deploy for one means the word waits for a deploy.

An extraction review has the same two halves, and they are not the same
half.

DECLARED, BECAUSE THE PIPELINE READS THEM
-----------------------------------------
The verbs, and the status each content type writes. `TYPE_BECOMES` maps
"the reviewer says it is a photo gallery" to a status every later stage
reads, and a type with no status behind it cannot be written at all --
the submit path refuses it. Adding one is a change to the pipeline's
vocabulary, which is a change somebody reviews.

REVISABLE, BECAUSE THEY ARE WORDS
---------------------------------
What each type and flag is CALLED, and what the flag means in a
sentence. "Out of scope" named the status; "Non-local" names what a
reviewer is saying. That correction was a deploy, and it did not need to
be one.

Rows override the declaration; with no rows the declaration answers, so
an empty table is the shipped vocabulary rather than no vocabulary.
"""

from django.core.cache import cache

#: The vocabularies this module keeps, as they are named in the rows.
TYPE_WORDS = "extraction_type"
FLAG_WORDS = "extraction_flag"

#: Matches datasets/terms.py: long enough that a page of rows does not
#: ask per row, short enough that somebody revising a word sees it take
#: effect while they are still looking at the page.
_HELD_SECONDS = 60


def _rows(vocabulary):
    """{value: (label, hint)} from the rows, or {} where none are kept."""
    key = f"review.vocabulary.{vocabulary}"
    held = cache.get(key)
    if held is not None:
        return held

    from datasets.models import VocabularyTerm

    found = {
        row.value: (row.label or row.value, row.spelling or "")
        for row in VocabularyTerm.objects.filter(vocabulary=vocabulary, retired=False)
    }
    cache.set(key, found, _HELD_SECONDS)
    return found


def forget():
    """Drop what is held, so a revision is visible on the next read."""
    for vocabulary in (TYPE_WORDS, FLAG_WORDS):
        cache.delete(f"review.vocabulary.{vocabulary}")


def content_types():
    """The type list as the queue offers it, with revised labels applied.

    The values and the statuses behind them are the declaration's; only
    the words are read from the rows. A row naming a type that no longer
    exists is ignored rather than offered -- it would be a button whose
    value the submit path refuses.
    """
    from review.dispositions import CONTENT_TYPES

    words = _rows(TYPE_WORDS)
    return tuple(
        {**entry, "label": words.get(entry["value"], (entry["label"], ""))[0]}
        for entry in CONTENT_TYPES
    )


def flag_words(flag, declared_label="", declared_hint=""):
    """(label, hint) for a flag, revised where somebody has revised it."""
    label, hint = _rows(FLAG_WORDS).get(flag, ("", ""))
    return (label or declared_label or flag, hint or declared_hint)


def declared_flags():
    """Every flag the queue can raise, with its shipped words.

    Read from review/queue.py rather than restated, so a flag added there
    appears here without a second edit -- the drift this page exists to
    make visible would otherwise start on the page itself.
    """
    from review.queue import FLAGS, SCOPE_EXCLUDED_FLAG

    seen = {}
    for _reason, (flag, hint) in FLAGS.items():
        seen.setdefault(flag, hint)
    seen.setdefault(SCOPE_EXCLUDED_FLAG, "Excluded as about somewhere else")
    for flag, hint in (
        ("minimal_capture", "Body is too short to be a story"),
        ("doubted_type", "The detector barely believed its own call"),
        ("flagged", ""),
    ):
        seen.setdefault(flag, hint)
    return tuple(sorted(seen.items()))
