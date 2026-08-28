"""What a newsroom is, and how often it publishes.

The directory records both as free text and spells neither consistently:
'digital native' beside 'digital_native', 'weekly' beside 'Weekly'.

Two readers need this and need it to agree. The builder folds case and
separators so one kind is one filter; the sources review queue reads the
same vocabulary to say that a record spelt differently is a record to
fix. Folding is not fixing -- it makes the filter usable today and leaves
the defect visible, which is the queue's job rather than a display trick.

Beside `datasets/owners.py`, which does the same for who owns a
publication, and for the same reason: what a value means belongs where
everything reading it can see it.
"""


def fold_value(value):
    """One recorded value, with case and separators taken out of it."""
    text = str(value or "").replace("_", " ").replace("-", " ").replace("/", " / ")
    return " ".join(text.split()).lower()


#: (key, what a reader sees, the spelling the corpus uses, folded values
#: this covers). The spelling is written here rather than counted, so a
#: run of new records spelt badly cannot make the bad spelling canonical.
#:
#: Words, separated by spaces. Taken from whichever spelling the most
#: records happened to carry, these came out inconsistent with each other
#: -- `digital native` beside `video_broadcast` -- so the queue proposed
#: an underscore for one kind and a space for another and read as though
#: the underscore were the correct form of that word.
#:
#: Nothing reads the underscore. The only code comparing against
#: `video_broadcast` is the crawler's coverage-radius calculation, and it
#: reads a legacy `sources/publinks.csv` rather than this column.
PUBLISHER_KINDS = (
    ("digital", "Digital", "digital native", ("digital native", "digital")),
    ("print", "Print", "print native", ("print native", "newspaper", "print")),
    ("tv", "Television", "video broadcast", ("video broadcast", "television", "tv")),
    ("radio", "Radio", "audio broadcast", ("audio broadcast", "radio")),
    # Ten records say only "broadcast", which is not an answer to whether
    # this is a television station or a radio one. Its own entry rather
    # than a guess into either, and no spelling to propose: what is
    # missing is the answer, not the wording.
    ("broadcast", "Broadcast, not said which", "", ("broadcast",)),
)

#: The same shape for how often a newsroom publishes.
PUBLISHER_FREQUENCIES = (
    ("daily", "Daily", "daily", ("daily",)),
    ("weekly", "Weekly", "weekly", ("weekly",)),
    # Bi-weekly, tri-weekly, semi-weekly and "weekly/daily" are one group
    # to filter by and four different answers to how often something
    # publishes. No spelling to propose across them: bi-weekly is not a
    # misspelling of tri-weekly, and offering one as a fix for the other
    # would put a wrong value in front of a reviewer as the right one.
    (
        "semiweekly",
        "More than weekly",
        "",
        (
            "bi weekly",
            "semi weekly",
            "tri weekly",
            "weekly / daily",
            "biweekly",
            "semiweekly",
        ),
    ),
    ("monthly", "Monthly", "monthly", ("monthly",)),
    ("continuous", "Continuous", "continuous", ("continuous",)),
)

#: Which vocabulary belongs to which field.
GROUPED_VALUES = {
    "publisher_type": PUBLISHER_KINDS,
    "publisher_frequency": PUBLISHER_FREQUENCIES,
}

#: Values that name a group without answering the question. "broadcast"
#: does not say television or radio; "weekly/daily" gives two answers to
#: one question. Both are records to fix and neither has a fix to
#: propose, so the queue asks rather than offers.
INDISTINCT = {
    "publisher_type": ("broadcast",),
    "publisher_frequency": ("weekly / daily",),
}


def group_of(field, value):
    """The key a recorded value groups under, or "" for one it does not.

    A value nobody grouped is not an error and is not dropped: the caller
    offers it under its own name.
    """
    folded = fold_value(value)
    if not folded:
        return ""
    for key, _label, _spelling, covered in GROUPED_VALUES.get(field, ()):
        if folded in covered:
            return key
    return ""


def spelling_of(field, value):
    """The spelling the corpus uses for this value's group, or "".

    Empty where the value is already written that way, where its group
    has no one spelling to offer, or where nothing recognises it -- the
    caller must not propose a change it cannot name.
    """
    group = group_of(field, value)
    if not group:
        return ""
    for key, _label, spelling, _covered in GROUPED_VALUES[field]:
        if key == group and spelling and spelling != str(value).strip():
            return spelling
    return ""


def is_indistinct(field, value):
    """True where the value names a group without answering the question."""
    return fold_value(value) in INDISTINCT.get(field, ())
