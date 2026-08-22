# What the review system is for

Referred to before changing anything in `review/`. Written 2026-08-22
after several passes that drifted from it.

## The goal

**Find every record that is potentially incorrect or incomplete,
categorize each problem under a controlled vocabulary, and present it to
an admin for review and disposition.**

Four parts, in order. Getting the order wrong is what produced a queue
full of rows labelled "nothing wrong with it".

### 1. Find

The app scans the corpus and decides what is wrong with it. Coverage is
the point: every record that might be incorrect or incomplete should
surface, not only the ones some file happened to mention.

A scan runs over the data, not over an import. An imported spreadsheet
is **evidence** — it can supply a candidate value for a flagged record,
or raise a flag by disagreeing with what is recorded — but it never
decides what belongs in the queue. A record with nothing wrong does not
appear because a file mentioned it.

### 2. Categorize

Every flagged record carries a **flag** from a controlled vocabulary.
A flag is not a description written at the moment it fires; it is a
named category with a definition, and the set it collects is exactly
what its name says.

Rules for the vocabulary:

- A flag names a defect in a record: something missing, something that
  does not exist, something contradicted. It never names the state of a
  proposed edit or the confidence of a check.
- Its name is precise enough to be a filter. Choosing it shows exactly
  the records that have that defect and no others.
- The list grows as defects are learned. Adding one is deliberate — a
  definition and a check that raises it — never a phrase invented in
  passing.
- Nothing enters the queue without a flag. If no flag fires, the record
  is fine and the reviewer never sees it.

### 3. Present

Flagged records go to the reviewer grouped **by record**, with every
flag on that record together and the evidence needed to judge it —
current values, candidate values, and where each came from. One
publisher with four problems is one card, not four rows.

Filters are the flags themselves, so a reviewer can work one kind of
defect at a time.

### 4. Disposition

The reviewer decides. Decisions are recorded against the record —
who, when, which flag, what they chose — and are durable: a decision
survives a rescan, and the scan does not raise the same flag again
after a human has ruled on it.

Deciding is not required in bulk. A reviewer may act on ten of ninety
and submit; the rest stay flagged.

## What this rules out

- Queue items that mean "an import proposed something" rather than
  "this record has a problem".
- Categories like "ready to apply" or "passes every check": those
  describe a proposed edit, not a defect, and a reviewer looking at that
  filter rightly asks why any of it is in a queue.
- Flags invented per finding, so two runs produce two vocabularies and
  no filter means anything across time.
- A rescan or a reload re-asking a question a person already answered.
