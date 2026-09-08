"""One syndicator, one label, however the corpus spells it.

`articles.wire` records the Associated Press under three names and TV
Insider under two, because each detection method writes what it found:
a byline says "Associated Press", a dateline says "AP National", a
canonical tag says "tvinsider.com". Counted as written, March 2026 reads

    The Associated Press   1,725
    Associated Press         598
    AP National              330
    tvinsider.com            438
    TV Insider               437

which is two syndicators shown as five, and a service filter that offers
each spelling separately so every one of them finds part of the work.

The pattern is `datasets/publishers.py`: groups seeded in code, rows in
`VocabularyTerm` overriding them, and `fold_value` taking case and
separators out of the comparison. Seeded rather than counted, for the
reason that file gives -- a run of records spelt badly must not be able
to make the bad spelling canonical, and here the two AP variants are 928
rows against the right name's 1,725.

Nothing is renamed on the article. This folds at the point of reading,
so a row still records what its detector actually found and a reviewer
can still see it.
"""

from datasets.publishers import fold_value

#: (label, the spellings it covers). The label is what a reader sees.
#:
#: Only groups where the corpus demonstrably holds more than one spelling
#: of one syndicator. A name appearing once needs no group, and inventing
#: groups for the 287 hosts that appear under a domain alone would be the
#: controlled vocabulary this deliberately does not have -- the domain is
#: the syndicator of record where nothing else names it.
SEED = (
    (
        "The Associated Press",
        ("the associated press", "associated press", "ap national", "ap"),
    ),
    ("TV Insider", ("tv insider", "tvinsider.com")),
    ("CNN", ("cnn", "cnn newssource", "cnn wire")),
    ("The Missouri Independent", ("the missouri independent", "missouri independent")),
)

#: The vocabulary these groups are kept in, once somebody edits them.
SYNDICATOR_WORDS = "syndicator"


def _seeded():
    """{folded spelling: label} from the seed."""
    return {
        fold_value(spelling): label
        for label, spellings in SEED
        for spelling in spellings
    }


def groups():
    """{folded spelling: label}, rows overriding the seed.

    With no rows at all this answers from the seed rather than folding
    nothing, which is the fallback `datasets/publishers.py` describes.
    """
    folded = _seeded()
    try:
        from datasets.models import VocabularyTerm

        for term in VocabularyTerm.objects.filter(
            vocabulary=SYNDICATOR_WORDS, retired=False
        ):
            label = term.label or term.spelling or term.value
            folded[fold_value(term.value)] = label
    except Exception:
        # A missing table or an unreachable database is not a reason to
        # show five Associated Presses.
        pass
    return folded


def label_for(name, folded=None):
    """What to call this syndicator, or the name itself.

    The name itself is the answer for everything ungrouped, which is most
    of them: 287 hosts appear that nothing else records, and
    `tvinsider.com` standing as its own label is the rule rather than a
    gap.
    """
    if not name:
        return name
    known = groups() if folded is None else folded
    return known.get(fold_value(name), name.strip())


def spellings_of(label):
    """Every spelling a label covers, including the label itself.

    The list offers one Associated Press and the corpus holds three names
    for it, so a filter matching only the label finds 1,725 of 2,653 and
    looks like the answer. Anything ungrouped returns just itself, which
    is what it was before this existed.
    """
    if not label:
        return []
    wanted = fold_value(label)
    found = {label.strip()}
    for spelling, name in groups().items():
        if fold_value(name) == wanted:
            found.add(spelling)
    return sorted(found)
