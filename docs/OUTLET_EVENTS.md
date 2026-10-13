# Outlet events

The outlet registry (`outlet_registry`, imported from the crawler's
`mo_outlet_registry.csv`) says what each outlet is **now**. The crawler's
builder computes it from `sources` and the directory lists and publishes each
rebuild to `gs://mizzou-news-maps-data/registry/mo_outlet_registry.csv`;
`manage.py import_outlet_registry` reads it from there. No rebuild goes
through a pull request. It keeps no
history: an owner is overwritten when a paper is sold, a status when it
closes. `outlet_event` is the history -- one row per thing that happened to an
outlet, never overwritten -- so that ownership, mergers, closures and launches
can be drawn over time.

## What an event is

| Field | Holds |
|---|---|
| `outlet_id` | The registry id. An outlet we crawl carries its `sources.id`. Blank for an outlet not in the registry (a paper that closed before the registry was built). |
| `outlet_name` | The outlet as it was named when the event was recorded. |
| `event` | `sold`, `merged`, `closed`, `launched`, `relaunched`, `renamed`, `owner_changed`, `correction`, `status`, `retraction`. |
| `effective_date`, `date_precision` | When it happened in the world, not when it was typed in. Precision is `day`, `month`, `year`, or `by` (on or before that date -- a story reporting it as done). Blank when nobody knows. |
| `from_owner`, `to_owner` | The ownership change, where there is one. |
| `merged_into` | What absorbed a merged outlet: an outlet id or the site it now appears at. |
| `new_name` | A renamed outlet's new name. |
| `new_status` | What a `status` event says the outlet is: `active`, `replica`, `print`, `social`, `duplicate`, `merged`, `closed`, `legal`, `business`, `shopper`, `magazine`. |
| `evidence_url`, `note` | Where it came from. |
| `sets_current` | Whether the event also describes the outlet as it is now. See below. |
| `retracts` | The event this one withdraws. |
| `recorded_by`, `recorded_at`, `origin` | Who entered it, when, and how (`form`, `source edit`, `backfill`). |

### A change is not a correction

"Weston Chronicle sold to Megan Jantos, 2026" is something that happened. "The
Unterrified Democrat has belonged to Warden since 2018 and our record said Voss"
is our data catching up. A timeline draws the first at its date. The second is
a `sold` event dated 2018 -- the sale -- and not a 2026 sale, which is what an
owner field overwritten in 2026 would imply. `correction` is for a fix with no
event behind it: a placeholder owner replaced with a name, a misspelling.

### Nothing is edited or deleted

An event is written once. A wrong one is withdrawn by a `retraction` that
names it, and both stay: the record of what we believed and when is part of
the history. The model refuses an update or a delete.

## The current state

The registry import still sets every outlet from the crawler's file. An event
recorded with `sets_current` is then applied on top, in the order the events
were recorded, so a sale entered here shows on the map and in the tooltips
without waiting for a registry rebuild:

| Event | Sets |
|---|---|
| `sold`, `owner_changed`, `correction`, `relaunched` with an owner | `owner` |
| `merged` | `status` merged, `merged_into`, off the map |
| `closed` | `status` closed, off the map |
| `relaunched` | `status` active |
| `renamed` | `name` |
| `status` | `status`, its basis (the note), `merged_into` for a merged or duplicate outlet, and where it is drawn: `active` by what we collect, `replica` / `print` / `social` in their own colour, the rest off the map |

### A reviewed status is an event

What an outlet *is* -- a replica edition, print only, a duplicate of another
row, a legal-notice sheet -- is a reviewer's word, and it is recorded as a
`status` event on the outlet's page, not written into the registry file. With
"how the outlet stands now" ticked it replaces the status the file gives the
outlet, and every later import lays it back on top. A wrong one is retracted
like any other event, and the outlet returns to what the file says.

For an outlet we crawl, an owner change is also written to `sources.owner`
through `audited_update`, so the crawler's own record and the registry rebuilt
from it agree.

A backfilled event is history only: its `sets_current` is off, because the
registry already holds the outcome.

### Every owner edit is an event

`audited_update` records an `owner_changed` event for every source whose
owner it changes, whatever page made the change. Its date is unknown -- the
edit says what the owner is, not when it changed -- and a reviewer who knows
can record the dated event beside it.

## Entering events

- **Outlets** (Data group): the registry, searchable; each outlet's page is
  its timeline and a form for one event at any date.
- **Record an event for an outlet not in the registry**: the same form with a
  name instead of an id.
- **Backfill**: `manage.py import_outlet_events <csv>` loads many at once.
  A row identical to an event already recorded is skipped, so a file can be
  loaded twice. The seed at `visuals/data/outlet_events_seed.csv` holds the
  events reported in the Missouri Press Association's stories, 2018-2026.
- **Events read from the press**: candidates waiting for review, below.
- **Export**: `/outlets/events/?format=csv` is every event, for a timeline.

## Events read from the press

Missouri Press News, the Missouri Press Association's magazine, reported
sales, mergers, closures, launches and renamings of Missouri papers from
1933 to 2005. The State Historical Society of Missouri hosts its scans with
their OCR text. A model read every passage that uses the language of a
transaction and proposed the events it states, each with the words on the
page that say it. Those proposals are `history_event_candidate` rows, and
none of them is history: a reading can be a Kansas paper, a website launch
taken for a founding, or "the Herald" with no town.

| Field | Holds |
|---|---|
| `publication`, `issue`, `page`, `link` | Where it was read: the first issue that reported it, and the hosted page. |
| `all_links`, `mentions`, `last_reported` | Every page that reported the same event -- "years ago" columns retell old sales. |
| `paper`, `town`, `county`, `names_in_press` | The paper as the press named it. |
| `known_title`, `matched_to`, `match`, `outlet_id` | What it was matched to: the Historical Society's title list (`shsmo`) or the registry (`registry`, with its outlet id), and how. |
| `event_type`, `year`, `year_basis` | The reading's event. `stated` is a year the page gives; `first report` is the year of the issue that reported it. |
| `from_owner`, `to_owner`, `quote` | The reading's parties, and the words on the page. |
| `flags` | What the reading could not settle: a paper not placed in a town, a quote matched through OCR noise, a title the lists hold only in another town, a website. |
| `status`, `reason`, `outlet_event`, `reviewed_by`, `reviewed_at` | The decision. |

**Outlets > Events read from the press** (`/outlets/press/`) is the queue,
filtered by status, event, match, flag and county. Each candidate shows its
quote and a link to the page.

- **Accept** records an outlet event through the same rules as the form, with
  the reviewer's corrections, the page as its source and origin
  `Missouri Press News review`. Its note says what happened in one sentence
  ("E. E. Swain, Sr. and Walter Ridgway bought the Kirksville Daily Express
  in 1909."), then cites the page and quotes it: the quote's sentence and,
  where the year is stated elsewhere, every sentence back to the one that
  states it. It is history only: `sets_current` is off,
  because the registry already holds what came after. A stated year is
  recorded at year precision; a first report is "on or before" the end of the
  issue's month.
- **Reject** keeps the candidate with the reason, so the same reading is not
  proposed again.

A decision is made once. An accepted event that turns out wrong is retracted
like any other outlet event.

| Reading | Outlet event |
|---|---|
| `ownership_change` | `sold` |
| `lease` | `owner_changed`, the note saying leased |
| `merger` | `merged`, the reading's `to` as `merged_into` |
| `founded` | `launched` |
| `closed` | `closed` |
| `renamed` | `renamed`, the reading's `to` as `new_name` |
| `moved` | none: the reviewer chooses |

Candidates are added with
`manage.py import_history_event_candidates <events.csv> --publication "Missouri Press News"`.
A reading already held is skipped, so a file can be loaded twice; a file with
any bad row adds nothing.
