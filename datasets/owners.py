"""Owner names, matched against the vocabulary the corpus already holds.

The corpus's owner values are clean and consistently cased
("CherryRoad Media", "Rust Communications"); spreadsheets carry the
degraded copies ("cherryroad", "rust communication", "Whitaker
Publishing."). So the corpus is the vocabulary and an incoming value is
matched to it, never the other way round.

Three outcomes:

  match      the value is a known owner under a different spelling;
             the corpus's spelling is what gets written
  conflict   the record already names a different owner — an ownership
             change is a fact to confirm, not a spelling to fix
  unknown    no known owner resembles it; reported, never written, so a
             lowercase fragment cannot enter the vocabulary by import
"""

import difflib
import re

# Owners confirmed by hand that the corpus does not yet record. Adding a
# name here is a deliberate act; it is not grown by importing.
SEED = (
    "Whitaker Publishing",
    "Sexton Media Group",
    "CherryRoad Media",
    "Rust Communications",
)

_PUNCT = re.compile(r"[^a-z0-9]+")


def fold(value):
    """Compare owners without case, punctuation or a trailing period."""
    return _PUNCT.sub("", (value or "").strip().lower())


def clean(value):
    """Tidy a value without inventing anything: whitespace and a
    trailing period only."""
    return re.sub(r"\s+", " ", (value or "").strip()).rstrip(".")


def canonical_owner(value, known):
    """(canonical, kind) for an incoming owner against known spellings.

    kind is "match" or "unknown". `known` is every owner the corpus
    records, plus SEED.
    """
    text = clean(value)
    if not text:
        return "", "unknown"
    folded = fold(text)
    table = {}
    for name in list(known) + list(SEED):
        table.setdefault(fold(name), name)
    if folded in table:
        return table[folded], "match"
    # "cherryroad" for "CherryRoad Media": a prefix is a match when only
    # one known owner starts that way and the fragment is long enough to
    # mean something.
    prefixes = [
        n for f, n in table.items() if len(folded) >= 6 and f.startswith(folded)
    ]
    if len(prefixes) == 1:
        return prefixes[0], "match"
    # "rust communication" for "Rust Communications": a near-miss, held
    # to a high bar so unrelated owners never merge.
    close = difflib.get_close_matches(folded, list(table), n=1, cutoff=0.92)
    if close:
        return table[close[0]], "match"
    return text, "unknown"
