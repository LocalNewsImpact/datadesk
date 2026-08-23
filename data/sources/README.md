# Source documents

Outside records about publishers, kept as dated readings.

A reviewer disposing of a conflict months from now needs to see what a
directory said **on the day it was read**, not what it says today. So
each reading is a file named for its date and is never edited in place:
a later reading is a new file beside it, and the difference between two
files is itself information.

Nothing here is authoritative. These are **evidence** — they can supply
a candidate value for a flagged record, or raise a flag by disagreeing
with what the corpus holds, but no field here is written to a publisher
record without a person deciding it in the review queue (`REVIEW.md`).

## Missouri Press Association

`mopress-<date>.json` — the machine-readable reading, what
`scan_sources --evidence` consumes.
`mopress-<date>.csv` — the same records flat, for opening while deciding
whether to believe a proposal.

Read with `manage.py fetch_mopress --fetched <YYYY-MM-DD>` from
<https://mopress.jumbl.app/contactmanager/contact/publicdirectory>,
including each entry's "More Information" drawer.

Per entry: name, website, ownership, county, mailing address, phone,
circulation, publication days, and the drawer's raw label/value pairs
under `extras`.

**MPA's own caveat, which the queue inherits:** the directory is
supplied to MPA by third parties and MPA does not warrant that it is
accurate or current. A disagreement between this file and the corpus is
a question for a person, not a correction to apply.

Entries are MPA *members*, so the directory is neither a superset nor a
subset of any dataset's publishers: it omits non-member outlets, and it
includes members that are not in the corpus. `contact_type` separates
publications (1) from individuals and associate members (2–7).
