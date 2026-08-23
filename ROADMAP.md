# Roadmap

Work queued after the Phase 0–5 build (SCOPE.md). Each item states what
it changes, what it touches, and what must be decided before it starts.

## 1. Dataset-scoped roles

**Now:** three global groups (viewer/editor/admin). A role grants the
same access to every dataset.

**Wanted:** access is per dataset, and the privilege within a dataset is
one of read / write / design; admin is global.

**And per application, decided 2026-08-23.** The suite shares one set of
users; what a person may do is granted per application, so the same
person can be an editor in Datadesk and a reviewer in the directory.
That adds a dimension this item was not carrying, and not every
application has datasets — the directory has collections, a later one
may have neither — so the scope has to be optional rather than always a
dataset.

**Model:**

```
Grant(user, app, scope, role)   role: a name from the table below
                                scope: a dataset slug, or null for
                                       the whole application
User.is_admin                   global, every app, every scope
```

**Two levels, not two vocabularies.** A *role* is a name — viewer,
editor, reviewer, designer, admin. A *privilege* is what it permits:
read, write, design. A role is defined as the set of privileges it
carries, so the grant stores the role name and the definition lives in
one place rather than being re-derived at each check:

| role | read | write | design | notes |
|---|---|---|---|---|
| viewer | ✓ | | | reads and exports |
| designer | ✓ | | ✓ | a viewer who also authors and publishes visuals |
| reviewer | ✓ | ✓ | | dispositions only, not imports (item 6) |
| editor | ✓ | ✓ | ✓ | full rights on the dataset; starts datasets |
| admin | ✓ | ✓ | ✓ | the same, everywhere, plus user admin |

**Designer is viewer plus design, and that is the whole of it.** It holds
`read`, so it reads and exports everything a viewer does; it adds
`design`; it does not hold `write`, so a designer never sees a
disposition to make. Reading the table down the `write` column is reading
who the review queue is for.

**Editor is the person who starts a dataset and then owns it, decided
2026-08-23.** Full rights on that dataset — read, write and design — and
commonly a viewer on everyone else's. So editor and admin carry the same
three privileges and differ by reach rather than by power: an admin holds
them in every application at every scope, and administers users.

That makes "editor or admin?" a question almost no check should ask. The
question is "may this person do this here", and the scope answers it.

**Administration is the only thing left that is not a privilege.** Users,
roles and the audit log are not scoped to a dataset, so that check asks
about the role. Everything else — reading, correcting, importing,
starting a dataset, authoring a visual — asks for a privilege on a scope.
That is what lets a new application or a new role be a row in a table
rather than a change to any view.

**Export rides on `read`, not on `write`.** Anyone who may see a dataset
may take it away — the deliverable CSVs in `review/exports.py` are the
shape the research is published in, and withholding them from the people
doing the research would make `read` mean "look at a page". This matches
what the code does today, where the export views carry `@role_required`
rather than an editor check; what changes is that the export is scoped to
the datasets the person can read instead of being all-or-nothing.

**The roles are per dataset, so one person holds several.** The common
case is exactly the one that motivates a designer: viewer or designer
across the datasets someone browses and builds visuals from, editor or
admin on the one they own.

**An editor may change anything inside their own dataset**, publishers
included. A publisher is shared between datasets, so an edit to one
becomes a proposal to the other datasets holding it, and a dataset's
public flag decides whether its publishers are visible outside it at
all — see item 10, where both live. `Grant` is keyed on (user, app, scope) with
one role per scope, so that is the ordinary arrangement rather than a
special case — and no precedence rule is needed, because the scopes
differ.

**Reviewer versus editor, decided 2026-08-23: `write` corrects what is
there, `create` brings new data in.** A reviewer works the review queue
and fixes the records in front of them. An editor does that and may also
import — which does not correct a record, it adds records that were not
there. Two different powers, so two privileges, and the table grows a
fourth column rather than a special case.

An earlier draft made this a role test, on the grounds that no privilege
expressed "how many records one click changes". That framing was wrong:
the difference is not volume, it is whether anything new arrives. Named
properly it collapses into the same vocabulary as everything else, and no
check has to mention a role name.

Starting a dataset is the same privilege, asked without a scope — the
dataset does not exist yet, and both an import and a new dataset bring
something into existence.

The rest of the definitions are stable. The set of privileges is small;
the set of role names will grow as applications join, and each new one is
a row in that table rather than a new concept.

**`is_staff` cannot express this.** It is one global boolean, and the
directory currently gates its admin on it. Per-application roles mean
that gate reads a grant instead — see item 14, where one shared admin
has to filter by application for the same reason.

- **read** — explorer, dashboards, published visuals for that dataset
- **write** — read plus review, dispositions, imports for that dataset
- **design** — read plus authoring and publishing visuals for it
- **admin** — every dataset, plus user administration and dataset CRUD

**Touches:** `accounts.roles`, every `@role_required` / `@editor_required`
view, the explorer and enrichment grids (filter to permitted datasets),
the review queue (item 6), the cost dashboard (item 5), the visuals
registry (a visual belongs to a dataset), the audit log (unchanged —
still records the actor).

**Decided, clarified 2026-08-23: a visual belongs to the datasets it is
wired to, and is owned by the account that made it.** Not "the datasets
its author could reach" — that was the earlier wording and it was wrong,
because it would attach a visual to datasets it never touches. What it
records is its own wiring.

Ownership does the constraining, and does it naturally: an author can
only wire a visual to data they can already read, so the set a visual
carries is a subset of what its owner held at the time. Nothing has to
check that twice.

A *published* visual is public at its embed regardless — publishing is
the act that makes it so, which is why publishing is a privilege
(`design`) rather than a side effect of authoring.

**Universal data belongs to no dataset.** FIPS codes, census tables,
demographics — reference data that is not anybody's corpus and is mostly
not loaded yet. A visual wired only to that is wired to no dataset, and
scoping must read that as "unconstrained" rather than as "no access".
The empty case means the opposite thing here than it does for a person's
grants, which is exactly the kind of inversion that is easy to get
backwards and worth stating before either is built.

**A visual belongs to a person first and to datasets second, decided
2026-08-23.** Someone can only build a visual from datasets they have
access to, so a publish-time check asking whether they hold `design` on
all of them re-asks a question that authoring already answered. The
invariant belongs at the point of authoring: a dataset the author cannot
read cannot enter the visual. Publishing then needs no per-dataset test
of its own.

**Revocation after authoring, decided 2026-08-23.** A grant is removed
and the visual now draws on a dataset its author can no longer read.
Nothing happens to the visual. A published visual stays published — the
embed does not break, because publishing was a deliberate public act and
revoking one person's access is not a retraction of it. What the author
loses is the ability to edit it further. From then on only an admin can
act on the visual.

So revocation changes who may edit, never what is public. The visual is
not orphaned and not withdrawn; it simply passes out of the author's
hands. That keeps the rule the same shape as the rest of this item —
access governs what a person may do, and publishing governs what the
world may see, and the two do not reach into each other.

**Cost visibility rides on `write`, decided 2026-08-23.** A person sees
the costs of the datasets they are an editor or admin on — not the ones
they can merely read, and not the ones they hold `design` on. Spend is a
management fact rather than a research one. The corpus dashboard shows
the union of the datasets a person can read; the cost figures on it show
the union of those they can write. Two scopes on one page, deliberately.

**The directory's admin gate is replaced, decided 2026-08-23.** Not
`is_staff` derived from a grant, and not `is_staff` left governing the
directory alone: the directory's admin routes through the same grant
check as Datadesk. It is the largest of the three options and the only
one where a person's access to the two consoles cannot drift apart. See
item 12, whose merge made one `auth_user` and left this the last thing
the two applications disagreed about.

## 2. Publish snapshots to Google Sheets

**Feasible.** Three mechanisms, in order of preference:

1. **Push on publish (recommended).** A service account writes a
   visual's or export definition's snapshot into a named spreadsheet
   tab via the Sheets API. The sheet is shared with the service account
   once; publishing (or a nightly refresh) rewrites the tab. Datadesk
   stays the source of truth and the sheet is a rendering of a pinned
   snapshot, so a reader cannot silently diverge from the embed.
2. **Pull from the feed.** An Apps Script in the sheet fetches
   `/visuals/<slug>/data.json` on a trigger. No credentials in Datadesk;
   the sheet's owner controls it. Only works for published visuals,
   since the feed is public only for those.
3. **Scheduled export job.** A Cloud Run job runs saved export
   definitions and writes tabs on a cron.

**Touches:** a `SheetTarget` model (spreadsheet id, tab, visual or
export definition, last written), the publish path, a new secret and
API enablement, `visuals/services.py`.

**Decided:** push on publish.

**Still open:**
- Overwrite the tab, or append a dated block? Overwrite keeps one truth
  and matches the pinned-snapshot model; append keeps history in the
  sheet and diverges from the embed.
- Which artefacts: visuals only, or saved export definitions too?
- Which spreadsheet — one per visual, or one workbook with a tab each?

## 3. Chart palette: muted, not primary

**Now:** the CIN taxonomy palette uses saturated hues. Against the
reference sample it reads as toy-bright.

**Wanted:** the sample's register — desaturated navy through mid blues,
olive, mauve, teal, tan, periwinkle. Lower chroma, closer values, still
clearing the adjacent-pair CVD floors in light and dark.

**Touches:** `TAXONOMIES.cin` and the four brand themes in
`static/js/datadesk-chart.js`; the palette validator gates every change.

**Constraint that shaped the current palette:** ten categories exceed
the eight-slot validated set, and the muted register narrows the gaps
further. Where a muted pair cannot clear the floor, the options are a
lightness step rather than a hue step, or folding two needs together in
the chart. Expect one or two pairs to need that.

## 4. Cost: billed, not reported

**Now:** the dashboard's headline is `article_enrichment.cost_usd` —
what the pipeline recorded. `openrouter_traces` is read for comparison
and the query's column names are still unverified against the external
table.

**Wanted:** billed cost is the number in the admin; recorded becomes the
comparison.

**Touches:** `explorer/costs.py`, the dashboard, the admin section.

**Decided 2026-08-23: tag the request.** The crawler can send a label
with each call, so a trace can carry the dataset it served. That turns a
per-dataset billed figure from an allocation into a measurement, and it
is the crux of item 5 answered — the allocation rule item 5 was waiting
on is not needed for the model spend, because the spend can say where it
came from.

**Where the label goes, checked against the live table 2026-08-23.**
`mizzou_analytics.openrouter_traces` has exactly one column — `trace`, a
JSON blob, Langfuse-shaped. A trace already carries `metadata` (a
populated dict, currently holding OpenRouter's own generation details),
`tags` (a list, empty on real requests), and `sessionId` and `userId`,
both null. So somewhere to put a dataset label exists today and nothing
writes one; the query would then group on
`JSON_VALUE(trace, "$.metadata.dataset")` or the equivalent.

**And the billed query has never worked.** `_BILLED_SQL` selects
`created_at`, `usage` and `cache_discount` as if they were columns of
that table. They are not — a dry run fails with `Unrecognized name:
created_at`. `billed_costs()` catches every exception and returns None,
so the dashboard has been showing the recorded side alone and saying
nothing. That is this item's "first step regardless", and it is now
confirmed rather than suspected.

Until it exists, billed cost is corpus-wide and shown only to an
application admin — a partial share of an untagged total would be a
guess presented as a number. `explorer/views.py` says so at the point it
withholds it.

**First step regardless:** verify the real `openrouter_traces` columns
and fix the query.

## 5. GCP compute cost, allocated by dataset

**Wanted:** attributable compute (crawl, extraction, enrichment) shown
per dataset to users with access, aggregated for admins.

**Mechanism:** BigQuery billing export, filtered to the crawler project,
allocated to datasets by a documented key.

**Decided:** compute jobs get a per-dataset label, so cost is measured
rather than apportioned. That work lands in MizzouNewsCrawler, not here:
every workload that serves one dataset carries `dataset=<slug>` as a
Kubernetes label, which the billing export surfaces as a resource label.
Datadesk then groups by it.

**Consequences of the labelling:**
- A workload serving several datasets at once cannot be labelled for
  one. Either it is split per dataset, or its cost stays in a shared
  bucket the dashboard shows separately — never silently divided.
- Cost before the labelling date is unattributable. The dashboard needs
  a "from" date and should say what predates it.

**Decided 2026-08-23: compute and storage, per dataset, close enough
rather than exact.** Both count as attributable and both are wanted.
Where a figure has to be estimated it may be, provided it is close and
provided the page says which numbers are measured and which are not — a
research budget needs to know what a dataset costs to about the right
order, not to the cent.

The shared lines stay shared. The Cloud SQL instance and the load
balancer serve everything, so they go in the bucket the dashboard shows
separately rather than being divided by a rule nobody would defend.

**What the whole cost picture is, across items 4 and 5:**

| | Source | State |
|---|---|---|
| Model spend, billed | OpenRouter, via `openrouter_traces` | Query broken; see item 4 |
| Model spend, recorded | `article_enrichment.cost_usd` | Works, and is per dataset today |
| Compute | GCP billing export, by Kubernetes label | Labelling not started |
| Storage | GCP billing export | Not started |
| Shared overhead | Cloud SQL, load balancer | Shown separately, never divided |

Billed is the number wanted for model spend; recorded becomes the
comparison, which is item 4's own framing.

## 6. Review queue scoped to access, and a reviewer role

**Now:** the queue shows every dataset to any role that can reach it,
and acting on an item writes to the corpus immediately.

**Wanted, three parts:**

1. The queue lists only datasets the user may act on, and an item is not
   actionable from a URL a user could otherwise guess.
2. A **reviewer** privilege between read and write: a reviewer works the
   queue and their dispositions are *proposed*, not applied. Someone
   with write on that dataset accepts or rejects them. This is how a
   student or a new hand works the queue without holding the corpus.

**Model:** a proposal is the same shape as an audit entry before it
happens — actor, target rows, field, before, after, reason — plus a
state (`proposed` / `accepted` / `rejected`) and the accepting user.
Accepting runs the existing audited write; the audit entry records the
accepter as actor and the reviewer as proposer, so both are attributable
and the revert path is unchanged.

**Touches:** `review/queue.py` and its templates, a `ChangeProposal`
model, the audit entry (a `proposed_by` column), and item 1's privilege
set, which becomes read / reviewer / write / design.

### The queue's own interaction

The queue today explains its cases in aggregate — the three cases carry
labels and notes, and the length bands say why an item is suspicious.
What it does not yet do is let a reviewer *finish* an item.

**Every row states two things, in its own words:**

1. **Why it is here.** Not the raw `skip_reason` string, but what that
   reason means for this article: "the stored text is a login wall, so
   the body is unusable — the byline and CIN label are not", "scope was
   excluded but recorded, and in March most of these were local stories
   that merely mentioned a foreign subject". The vocabulary exists
   already in `CASE_NOTES`; it belongs on the row, not only above the
   list.

2. **What can be done about it.** Whatever the case, the question put to
   the reviewer is the same one: *the app flagged this — do you agree,
   disagree, or fix it?* Three verbs, everywhere, so a reviewer learns
   the queue once rather than per case.

   The three verbs mean the same thing in every case, because each is a
   decision about where the article goes next:

   | | Meaning | Where the article ends up |
   |---|---|---|
   | **Agree** | the flag is right | out of the processing flow, terminally — it is done |
   | **Disagree** | the app is wrong | back into processing, unchanged, from where it was flagged |
   | **Fix** | the flag is right and I can repair it | repaired first, then back into processing |

   So disagree and fix differ only in whether the record is changed on
   the way back in. Agree is the only one that ends the article's
   journey.

   Agree and disagree are one click. Fix opens the edit — the text, the
   scope, or a re-extraction request — because a fix is a change and a
   change is reviewed like any other write.

   All three end the item's presence in the queue. Agree and disagree are
   both decisions; only "fix" is deferred work, and it leaves the queue
   because it has become a task with an owner rather than a question.

### Decide now, execute on submit

Every review queue — this one and the ones after it — works the same
way: the reviewer makes decisions down the page, and **nothing happens
until they submit**. A decision is a stated intention; submit is the
act. That is what makes it safe to click quickly through fifty rows.

- Each row carries the same control: agree / disagree / fix.
- Choosing one marks the row decided and leaves it visible, so it can be
  changed before submitting.
- A running count — "23 decisions pending" — and one submit for all of
  them.
- Submit executes every decision in the session as **one audited batch**,
  reverting as a unit, like an import batch.
- Leaving without submitting discards nothing quietly: the page says
  what is pending.

**A fix is not a decision — it is work.** Agree and disagree are
judgements about the record as it stands, so they stage as bare
decisions. A fix says the record is wrong and someone has to change it:
the reviewer opens the editor and makes the edit that removes the
reason the article was flagged. The edit stages with the session and
applies on submit like the rest, but the reviewer has to do it, and no
amount of clicking substitutes.

What a fix edits, and what makes it resolved:

| Case | Flagged because | A fix edits | Resolved when |
|---|---|---|---|
| Paywall stub | stored text is a teaser or login wall | the article text, or a re-extraction that replaces it | the text is the article's own |
| Minimal capture | little or no text captured | the article text, or a re-extraction | there is real text — the band it now falls in is no longer the flagging one |
| Scope mislabel | scope excluded but recorded | the scope value | the scope is one the profile does not exclude |

A fix that cannot be made — the text is genuinely gone, the source is
dead — is not a fix. It is an agree, and the reviewer should say so
rather than leaving the item half-worked.

**Submit checks that a fix actually fixed it.** After the edit applies,
the flag's own condition is re-evaluated against the new record. If it
still holds — text still under the threshold, scope still excluded —
the item does not leave the queue; it returns with "the edit did not
clear the flag", because it did not. Marking it resolved on the
strength of an edit that changed nothing is how a queue starts lying
about its own size.

**The metadata pattern does not carry to long text.** For a publisher's
city or owner, three columns and a text field work: the values are short
enough to compare at a glance and to retype. An article's body is
neither. A queue whose fix means replacing several paragraphs needs a
different affordance — the editor opening in place, or the row handing
off to the article view and coming back — and that is a separate design,
not a wider column.

**Fix does not bulk.** Agree and disagree apply to a selection; a fix is
one record at a time by nature, since the edit is specific to it. A
queue worked at speed is mostly agree and disagree, with fixes as the
few that need hands — and that ratio is worth showing, because a case
that is mostly fixes is a pipeline defect, not a review workload.

**Deferred execution has one real hazard: staleness.** Minutes pass
between the decision and the submit, and the pipeline does not stop. If
an article is reprocessed in that window, the state the reviewer judged
is not the state being written. So submit re-reads each row and, where
the record moved underneath, reports it rather than applying: "3 of 23
changed since you decided — review those again". The rest apply.
Without that check, deferred execution quietly launders stale
judgements into the corpus.

**Where pending decisions live** decides how far a session can stretch.
Held in the page, a session is one page of the queue and a refresh
loses it. Held server-side as drafts, a reviewer can work several bands
and pages and submit once — which is how the queue will actually be
worked, and is the reason to prefer it.

**Agree is the common case and must be one click.** A checkbox per row
and an "agree with selected" action on the page — "the app was right,
stop asking me". It records a disposition through the audited path, so
the decision carries a name and a time and can be reverted like any
other write, and the row leaves the queue.

**Disagree is the valuable case.** It is the reviewer saying the
pipeline was wrong, and it is the only signal that tells anyone whether
a flag is worth keeping. A case whose items are 90% disagreed is a
broken rule, not a queue; the disposition counts should be visible per
case so that shows up rather than being absorbed by patient reviewers.

### Who does the routing

Datadesk records the disposition; **the crawler acts on it.** Datadesk
does not reset pipeline status, requeue work or clear the pipeline's own
flags — it is a window and a control panel, not a data plane
(SCOPE.md §5). The orchestrator reads the review columns and routes:

| Disposition | What the pipeline does |
|---|---|
| `agree` | never reprocesses and never re-flags it; the article is terminal |
| `disagree` | clears the flag and resumes the article from the stage it was flagged at |
| `fix` | waits for the edit, then resumes as with disagree |

This keeps the write boundary narrow. Datadesk writes the five review
columns and, for a fix, the field being repaired — nothing that steers
the pipeline. If instead Datadesk reset `articles.status` or
`candidate_links.status` itself, it would be running the pipeline by
hand from a web form, and two systems would own the same state.

**Done (2026-08-22):** correcting a wrongly scoped article means
changing the `scope` value on its enrichment record. The database user
Datadesk writes with could not change that column, so the permission now
covers `scope` and `scope_confidence` as well.

`scope_confidence` is the model's own estimate of how sure it was. When
a person sets the scope, that number is erased rather than set to a high
value — a person's decision is not a prediction, and leaving a high
number there would make every human correction look like the model's
most confident guess in any chart or filter that uses confidence.

### Where a review is recorded

**Decided:** a review is columns on the record — reviewed, with a
reason and a disposition — not a filter Datadesk keeps to itself.

They belong on `article_enrichment`, in the crawler database, for one
reason: the pipeline has to see them. A disposition Datadesk keeps
privately stops the queue re-showing an item, but does nothing to stop
the pipeline re-flagging it on the next run, which is the same article
arriving back in the queue with a new row. The pipeline can only skip
what it can read.

```sql
ALTER TABLE article_enrichment
  ADD COLUMN reviewed_at             timestamp,
  ADD COLUMN reviewed_by             text,   -- actor, by email
  ADD COLUMN review_disposition      text,   -- agree | disagree | fix
  ADD COLUMN review_reason           text,   -- why, in the reviewer's words
  ADD COLUMN reviewed_profile_version integer;
```

`reviewed_profile_version` matters more than it looks: a judgement is
made against a particular version of the rules. "I disagree that this is
out of scope" was decided under profile v3; if v4 changes what scope
means, that judgement is no longer known to hold. Recording the version
lets a reviewed item resurface deliberately when the rule beneath it
changes, instead of silently standing on a decision nobody would make
again.

**Sequence, across two repositories:**

1. **MizzouNewsCrawler** adds the columns (alembic) and teaches the
   enrichment orchestrator to leave reviewed articles alone — an
   `agree` is final, a `disagree` is an instruction to re-run without
   the flag, a `fix` waits for the fix.
2. **Datadesk** grants `datadesk_rw` UPDATE on exactly those five
   columns (`infra/sql/create_crawler_write_role.sql`), writes them
   through the audited path, and filters the queue on `reviewed_at`.

Until step 1 lands, Datadesk can write nothing here — the columns do not
exist. A Datadesk-side table would unblock the UI sooner and would have
to be migrated into the columns later, so it is worth doing only if the
crawler change is far off.

**Bulk is the point.** Reviewers work in bands (the 2000+ band is mostly
false flags); selecting a band and accepting it in one action is the
difference between a queue that gets worked and one that does not.

**Decided 2026-08-23:**

- **A requested re-extraction keeps the item.** It moves to a *fix
  requested* state and stays there until the re-extraction lands and the
  condition clears. The queue never claims something is resolved while the
  article is unchanged, and two people cannot request the same fix. This
  needs a state the leave-on-request model does not, and that is the cost
  of being honest about an asynchronous resolution.
- **`review_reason` is a short list per case, plus an optional note.** The
  vocabulary aggregates — "why do reviewers reject this case" is
  answerable — and the note carries the unusual thing without forcing it
  into the nearest wrong bucket. The cases already have their language in
  `CASE_NOTES`, so the lists are written, not invented.
- **A disagreement feeds back to the pipeline.** Disagreeing clears the
  skip and lets the article re-enrich, which is what a reviewer expects to
  happen. It also means a wrong click changes the corpus, so it goes
  through the audited path: SCOPE.md's append-only log records it and
  revert-as-compensating-action undoes it, both of which already exist.
  Not a separate second action the reviewer has to remember.
- **Pending decisions are server-side drafts.** A review session spans
  pages, because paging is how the queue gets worked, and a closed tab
  should not cost an hour. That buys a table and a rule for cleaning up
  abandoned sessions, which is a smaller price than losing work.

**Follows from those, and from item 1:**

- **A reviewer sees the whole queue for the datasets they hold**, not a
  personal assignment list. Item 1 scopes access per dataset and has no
  notion of assigning a row to a person; adding one would be a feature
  rather than a filter, and nothing here needs it yet.
- **One audit entry per submitted session**, since a session is now a real
  object rather than whatever fitted on a page. The log stays readable and
  a batch reverts as a unit; revert already restores per row inside it, so
  the finer grain is available without being the default.

**Still open, and genuinely a judgement:**

- Does a reviewed item resurface when the profile version advances past
  `reviewed_profile_version`? It should be *possible* — a reviewer's
  answer was about a particular version of the pipeline's reasoning, and a
  new version can invalidate it. Whether it is the default is a question
  about how much churn a reviewer will tolerate, and that is better
  answered after watching one profile change than before. Default to not
  resurfacing, make it available per case, and revisit with evidence.

**Depends on:** item 1, which is decided.
## 7. Navigation performance on data-heavy pages

**Now:** moving between data-heavy tabs is slow. Each page renders its
full result set server-side before the browser sees anything, so the
grids, the dashboard and the cost page all pay their whole query cost
up front.

**Wanted:** the page frame arrives immediately and the heavy region
fills in.

### Measured 2026-08-23 — it is the query, and it is one missing index

The article grid's default sort is `publish_date DESC NULLS LAST,
created_at DESC`. There is **no index on `publish_date`**. Explaining the
first page against production:

    Limit  (actual time=4491.563..4497.427 rows=50)
      Buffers: shared hit=4294 read=57496
      ->  Gather Merge  (Workers Launched: 2)
            ->  Sort  Sort Key: publish_date DESC NULLS LAST, created_at DESC
                  Sort Method: top-N heapsort

**4.5 seconds to return fifty rows**, by sequentially scanning all
164,570 articles and reading 57,496 blocks off disk to sort them. Every
visit to the grid pays it, and so does every page of it.

This settles the order of the work below. **Deferring the region would
only move 4.5 seconds behind a spinner**; the frame would arrive fast
and the data would not. The index is the fix, and it is item 4 in the
list below rather than item 2.

    CREATE INDEX CONCURRENTLY ix_articles_publish_date_created
      ON articles (publish_date DESC NULLS LAST, created_at DESC);

Matching the ORDER BY exactly is what makes it usable; 2,352 of the rows
have a null `publish_date`, so the NULLS LAST is not cosmetic.

**Owned elsewhere.** `articles` lives in the crawler's database, which
Datadesk reads read-only. The index has to be added by
`MizzouNewsCrawler`, so this is a request to that repository rather than
a change here — and worth doing there before any of the work below.

Still to measure the same way: the enrichment grid, the review queue,
the dashboard and the cost page.

**Approach, cheapest first:**

1. **Measure before changing anything.** Which pages, and is the time in
   the query, the render, or the payload? A missing index and a slow
   template look identical from a stopwatch — as the measurement above
   shows, where the answer turned out to be neither rendering nor
   payload.
2. **Defer the heavy region.** The frame renders with the filters and a
   placeholder; the grid body arrives over htmx, which is already in the
   page. The pattern is the one the articles grid uses for filtering.
3. **Cache the counts.** Pagination's `COUNT(*)` over a filtered corpus
   is often as expensive as the page of rows; an approximate or cached
   count removes it from the critical path.
4. **Index what the filters actually sort and filter on**, once the
   measurements say which.

**Touches:** `explorer/views.py` and its templates, `review/queue.py`,
the dashboard, the cost page.

**Note:** the story map and other snapshot-backed visuals are already
fast — they serve a pinned snapshot rather than querying. The same trick
(precompute, serve the result) is available to the dashboard if its
numbers may be nightly-fresh rather than live.

## 8. Extraction page as a per-dataset operations view

**Now:** the extraction review queue lists items needing human
attention. There is no view of the pipeline that produced them.

**Wanted:** one page per dataset — selectable for anyone with access to
more than one — carrying:

- counts of sources and of articles in the database
- article and candidate-link counts by pipeline status
- pipeline health indicators
- cost for the dataset
- the human-review tasks outstanding, linking into them
- current logs for that dataset and job
- workers running, and articles discovered / processed / extracted

**What the corpus already supports (checked 2026-08-22):**

| Need | Source | State |
|---|---|---|
| Sources per dataset | `dataset_sources` | available |
| Articles per dataset | articles → candidate_links → dataset_sources | available |
| Status counts | `articles.status` (labeled 85k, wire 47k, enriched 14.5k, obituary, out_of_scope, weather, paywall, opinion), `candidate_links.status` (extracted 85k, wire 78k, paused 41k, not_article 26k, sampled_out 16k) | available |
| Discovered / processed / extracted | `jobs.records_processed`, `records_created`, `records_updated`, `errors_count`, `exit_status`, `started_at`, `finished_at` | available |
| **Job → dataset attribution** | `jobs.params->>'dataset_label'` — present on 767 of 769 rows | **available now**; no crawler change needed for this page |
| Extraction health | `extraction_telemetry_v2` — per article: success, HTTP status, error type, timings, methods attempted, proxy state | available |
| Recorded cost per dataset | `article_enrichment.cost_usd` joined through the dataset path | available |
| Human-review tasks | the review queue, filtered to the dataset | available after item 6 |
| Logs | `jobs.logs_path` | **unverified** — see below |
| Workers running | not in the database | **needs a decision** — see below |

**Two gaps, and the cheapest way to close each:**

1. **Logs.** `jobs.logs_path` exists on the job record. If it points at a
   bucket object, Datadesk reads it with a storage grant and the feature
   is small. If logs live only in Cloud Logging on the crawler project,
   Datadesk needs a log-reader grant there and a query by resource
   label, which is more plumbing and more privilege. **Check what
   `logs_path` actually contains before designing anything.**

2. **Workers.** Live cluster state is not in Postgres, and Datadesk
   should not hold cluster credentials — it is a window, not a control
   plane (SCOPE.md §5). Three options, cheapest first:
   - *In-flight jobs as a proxy*: rows with `started_at` set and
     `finished_at` null, grouped by dataset. Free, immediate, and
     approximately what "workers running" means to a reader.
   - *Cloud Monitoring read*: real pod counts, one more cross-project
     grant, and a metric to agree on.
   - *Crawler heartbeat*: the crawler writes worker counts to a table
     Datadesk reads. Most accurate, needs crawler-side work.

### What health means here

Healthy is: **articles are moving through the pipeline, and every
process the dataset's job asked for is actually being applied.**
Individual errors and questions are not health — they are the tasks
listed underneath it, for a human to work.

That definition is computable from what the pipeline already records.
Four indicators, each with the query behind it and its reading today:

**1. Flow — are articles advancing?**
Counts by `articles.status` and `candidate_links.status` per dataset,
with the age of the oldest row in each stage and throughput over a
window from `jobs.records_processed`. A stage whose oldest row keeps
ageing while its count holds steady is a stall, and a stall is the
thing this indicator exists to catch.

**2. Process completeness — is every requested step being applied?**
The dataset's profile says which steps it wants
(`datasets.metadata->enrichment_profile`); `article_enrichment.steps_applied`
says which ran. The indicator is the gap between them, per step.

Mizzou's profile v3 requests content_gate, scope, places, people,
organizations and the five metadata presets. Measured today:

| steps_applied | articles | reading |
|---|---|---|
| full set incl. places, focus | 8,365 | complete |
| **missing `places`** | 3,212 | a requested step is not being applied |
| full set, no focus | 2,596 | complete for the profile |
| `{}` — nothing | 4,883 | gated out or skipped; check against `skip_reason` |
| content_gate + scope only | 72 | stopped early |

So this indicator is not hypothetical: it already reads amber for
Mizzou on `places`.

**3. Profile currency — is reprocessing owed?**
The version-bump contract says the pipeline reprocesses articles whose
`profile_version` is below the dataset's current version. Today Mizzou
sits at v3 with 15,735 articles there — and 3,695 at v0 plus 24 at v2,
so 3,719 articles are owed reprocessing. That number should trend to
zero after a bump; if it does not, the reprocessing is not running.

**4. Job completeness — did every process the job asked for run?**
`jobs` grouped by `job_type` for the dataset: last success, last
failure, `exit_status`, `errors_count`. A job type the dataset expects
that has no recent success is the clearest unhealthy signal there is.

**Under the indicators: the tasks.** Individual errors and questions —
extraction failures, wire-check ambiguities, geography that could not be
resolved, articles a reviewer must judge — link into the review queue
filtered to that dataset (item 6). They are consequences to work, not
measures of health.

**Still to decide: the thresholds.** Each indicator needs a number that
means "unhealthy" — how stale is a stalled stage, what share of a
requested step missing is tolerable, how long may reprocessing lag.
A threshold nobody agreed to becomes an alarm people learn to ignore.

**Noticed while checking:** `steps_applied` contains a `focus` step that
the profile schema Datadesk mirrors does not know
(`datasets/profiles.py`). Either the pipeline gained a step since that
mirror was written, or focus is derived rather than configured. The
profile editor may reject a valid profile until this is resolved.

**Touches:** a new `explorer/pipeline.py` for the aggregates, the
extraction page and its template, item 1 for the dataset selector, item
6 for the review-task links, item 5 for compute cost.

**Performance warning:** this page aggregates over 164k articles and
85k candidate links per view. It is the page most likely to be slow, so
it should be built precomputed or cached from the start rather than
retrofitted — see item 7.

**Two of these are answered by item 19**, which builds the same panels
across all datasets and should build them first:

- `logs_path` points at Cloud Logging in the crawler's project, which
  Datadesk's runtime account cannot read yet — one grant, named in 19.
- The worker signal is the Kubernetes API on `mizzou-cluster`, not a
  database column, which is why 19 calls it the only piece with a real
  dependency.

**Still open:**
- The health thresholds, and who owns them.
- Whether `focus` is a profile step Datadesk's schema mirror is missing.

## 9. The sidebar in groups — done

**Was:** one "Work" list and one "Admin" list, with editor-only links
appended to Work because there was nowhere else for them.

**Shipped:**

```
Data          Articles · Enrichment · Visuals
Sources       Proposed changes · Import · Add a publisher
Extraction    Review queue
Admin         Cost · Datasets · Users · Roles · Audit log
```

"Auditing" became **Sources** and **Extraction**. "Proposed changes"
never said what it covered; under Sources it does — those are publisher
records — and Sources is where the news-source tools reach the nav.
Review queue is article extraction, so it sits under its own header with
room for the operations view (item 8).

**What the grouping changed beyond appearance:** the old list carried an
invariant the tests enforced — everything in Work open to any assigned
role, everything in Admin admin-only. Each group now declares the role
it requires, and a section may raise that bar for itself: "Add a
publisher" is admin-only inside an editor group, so an editor sees
Sources without it. `test_admin_access` walks the groups and checks each
section's declared role against the decorator on its view, so a link
moved between groups whose guard does not match fails the suite.

**Resolved by item 14.** There is a publisher directory: the Source
Directory, and the Sources group links to it at
`sources.localnewsimpact.org`. What remains is narrower than the original
note — `source_edit` is still reachable only from inside dataset detail,
so editing a publisher from Datadesk means finding a dataset it belongs
to first. Item 1 decides who may, and item 10 decides which of the two
records is authoritative; the link is no longer the missing piece.

## 10. One source of truth: merge the crawler's sources into the directory

**Now:** two tables describe the same publishers. The crawler's
`sources` is what the pipeline runs against and what Datadesk reads and
writes through the column boundary. The Source Directory
(`NewsSourceDirectory`, its own repository and database) is the record
of record, with the fields research needs and a public widget over it.
A publisher edited in one is unchanged in the other.

**Wanted, refined 2026-08-23:** a publisher record per dataset, each with
a UUID, and the crawler reading publishers rather than keeping its own
idea of who they are.

**The Source Directory is a list of publisher records, not an authority.**
It does not hold *the* record and it does not decide anything. It is
where the records are listed and read — a view over them, and the public
widget on top. Nobody proposes a change "to the directory"; changes are
proposed to the owner of a dataset, because owners are the only things in
this design that decide.

What makes it one source of truth is therefore not that there is one row.
It is that records describing the same outlet are *linked* by a decision
two owners made, and their differences are reconciled through the queue
rather than diverging quietly. The title still holds: it was always about
ending the state below, where a publisher edited in one place is
unchanged in the other and nothing notices.

**Why it is not a migration script:** the directory's schema does not
carry what the pipeline needs. Scoping has to answer, per column on
`sources`, one of three things — the directory already has it under
another name, the directory needs it added, or it is pipeline state
that does not belong in a record of publishers at all and should stay
crawler-side keyed by publisher id. `host_norm`, discovery
configuration, per-source crawl state and health are the ones to argue
about first.

**Sequence within the item:**

1. Column-by-column inventory of crawler `sources` against the
   directory's model. Produce the three-way classification above; that
   document is the actual deliverable of the scoping step.
2. Add the columns the directory is missing, in its own repository.
3. Backfill and reconcile — this is where every conflict the review
   queue has been collecting gets used, since the two tables disagree
   and a person has already ruled on many of those disagreements.
4. Point the crawler at the directory, keeping `sources` as a read
   view until the pipeline is proven against it.

**A publisher belongs to several datasets, and that is the whole
problem, decided 2026-08-23.**

An editor may change anything inside the dataset they own, publishers
included. But a publisher record is not inside one dataset — the same
outlet sits in several, and an edit made by one owner would silently
rewrite what the others see. Two rules follow.

**Which fields are even in question.** Not everything on a publisher
record is contested, and separating the three kinds first makes the
reconciliation below much smaller than it looks.

| | Lives on | Who decides | Reconciled? |
|---|---|---|---|
| **Identity** — name, domain, city, county, medium, owner, state, founded and closed dates | each dataset's publisher record | its owner, with differences proposed across links | yes — this is what the queue is for |
| **Dataset-scoped** — whether this outlet is in scope for *my* study, my notes, my classification of it | the membership row (`dataset_sources`) | that dataset's owner alone | never — they are not claims about the same thing |
| **Pipeline state** — bot sensitivity, RSS health, extraction method, discovery configuration | the crawler's `sources`, keyed by publisher | the crawler | never — it is not a fact about the publisher |

`dataset_sources` is `id, dataset_id, source_id` and two legacy columns
today, so the place for dataset-scoped facts exists and is empty. That
half costs a column rather than a design.

Only the first row is contested, which is what the identity and field
proposals below are for. An earlier draft of this item tried to avoid
contest altogether by insisting on a single publisher record with one
value per field. That is not the shape: datasets are owned separately and
each owner has to be able to correct their own data without waiting, so
the records are per dataset and the agreement is explicit rather than
structural.

**The public flag is what makes a prediction possible at all.** A public
dataset exposes its publishers' basic information — enough for the
proposal system to notice that an outlet in one dataset looks like an
outlet in another, and enough for the two owners to judge whether it is.
A dataset that shares nothing cannot be matched against, and nobody can
be asked to confirm a link they cannot see either side of.

So the flag is not a sharing nicety. It is the precondition for
reconciliation: without it every corpus is an island, and the queue has
nothing to propose.

That is what the flag is worth. Without it an editor cannot see that the
outlet they are correcting also appears elsewhere, and a corpus that
nobody can see the shape of is not a list of anything.

**Identity is a UUID, and it is never inferred, decided 2026-08-23.**

Every publisher added gets a publisher UUID. That is the identity, and it
is the only thing anything joins on. No process concludes that two
records are the same outlet by comparing hostnames or publication names —
that is the standing rule, and this is the reason for it: a hostname
changes, and the same hostname can front two publications.

**But prediction is allowed, and is the point.** The proposal system may
look at name and host and *predict* that two records in different
datasets are the same outlet. That prediction is a proposal, not a
conclusion. It goes into the disposition queue, and if both dataset
owners agree, the two UUIDs are **linked**.

So the machinery is: a computer suggests, two people confirm, and the
link is a recorded decision rather than a derived fact. Matching is
allowed to be fuzzy precisely because it is never trusted.

**Linked, not merged.** Both UUIDs survive. Each dataset keeps its own
publisher record; the link asserts that they describe the same outlet.
Once two records are linked, the differences between their fields become
proposals in the same queue — so reconciling what an outlet is called
follows exactly the path that established it is one outlet.

That is why this does not need a bridge table built by a matching script,
and why the 1,149 crawler sources and 2,809 directory outlets do not have
to be reconciled before anything else can happen. They are linked
incrementally, by the people who know, and an unlinked pair is simply not
linked yet.

**One queue, two kinds of proposal:**

| | Proposes | Confirmed by |
|---|---|---|
| Identity | these two UUIDs are the same outlet | both dataset owners |
| Field | this outlet's name/city/medium should be X | the owner of each dataset holding it |

Item 6 already builds the queue and the disposition model, and a proposal
is an audit entry before it is applied. Both kinds ride it. The
attribution requirement applies to both: whoever proposed is named, so
the person deciding knows whose judgement they are accepting.

**What this leaves to settle:**

- What a prediction is allowed to weigh, and what confidence is worth
  surfacing. A suggestion nobody trusts is noise; one that is right often
  enough becomes a rubber stamp, which is the same failure as deciding
  automatically.
- Whether a link can be broken, and what happens to the field proposals
  that flowed through it.
- What counts as *basic* publisher information — the fields a public
  dataset exposes. Name, domain, city, county, medium and owner are the
  obvious set; coverage records, review notes and data-quality issues are
  editorial and are not.
- Whether a rejected proposal is remembered, so the same suggestion is
  not made three times.

**Touches:** `NewsSourceDirectory` (schema and API), the crawler's
source access, and Datadesk's `explorer.models.Source` plus the write
boundary in `review/services.py` — the boundary would move to the
directory's API rather than Postgres column grants, which is a real
change to how the guarantee is enforced and should be decided
deliberately, not inherited.

**Feeds on:** the Missouri Press readings in `data/sources`, which are
already the outside evidence for the fields a publisher record carries.

## 11. Datasets in the directory, so a job can name one

**Now:** a dataset is a Datadesk and crawler concept —
`explorer.models.Dataset` with `DatasetSource` membership rows against
the crawler's `sources`. The directory has no idea datasets exist. So
the crawler cannot ask the directory for "the publishers in Missouri
Missouri-State" and run a job over them; membership only exists on the
side that is meant to stop being the record of record.

**Wanted:** the directory carries dataset membership, and the crawler
calls it to resolve a job's publishers.

**Why it needs investigation before design:** the crawler's coupling to
`sources` is tight and not only through membership — job parameters,
discovery and per-source state all reach into that table. The questions
to answer before proposing a schema:

- What does the crawler actually read from `sources` when starting a
  job, and which of those reads are membership as opposed to state?
- Is dataset membership a property of the publisher, a join table, or
  a query over publisher attributes? Today it is a join table; if
  Missouri membership is really "publishers in Missouri", an attribute
  query is closer to the truth and does not need maintaining.
- Can a publisher belong to more than one dataset, and does the crawler
  assume it cannot?
- What happens to a running job when membership changes underneath it?

**Sequence:** this is item 10's second half and should not start before
10's column inventory, since membership is one of the columns that
inventory has to classify. Doing them in the other order means
designing dataset membership against a schema that is about to move.

**Touches:** the crawler's job start path, `explorer.models.Dataset`
and `DatasetSource`, and item 1 — dataset-scoped roles resolve
membership too, so if membership moves, both resolve it from the same
place or they will disagree.

## 12. One sign-in across the suite — done

**Done 2026-08-23.** One Google client, one `auth_user`, one session
cookie on `.localnewsimpact.org`. Signing in to either console signs you
in to both.

All three steps below shipped. Step 3 went further than written: rather
than two applications sharing a database, item 14 made them one Django
stack, so the identity tables are not shared between processes — there is
one process per front end and one set of tables. The directory's 252,758
rows moved into the `datadesk` database under a `directory` schema with
ids intact, so no foreign key needed remapping.

The scaffolding step 3 called for — a router to keep the second
application from migrating `auth`, a `SHARED_IDENTITY` flag, a split
`SITE_ID`, a shared `SECRET_KEY` — is gone with it, except `SITE_ID`,
which stays because `django_site` is a table of hostnames and two
hostnames need two rows. Datadesk's deploy runs `migrate directory`,
naming the app, which is what makes the router unnecessary.

The cost named at the bottom of this item stands: one `SECRET_KEY` across
both, and the directory cannot be brought up without the shared database.

Two things this item wanted are still open, and belong to item 1:
reconciling `is_staff` with Datadesk's groups, so one of them governs and
`is_staff` is derived rather than set by hand.

**Was:** signing in to Datadesk did not sign you in to the Source
Directory. They are separate Django applications with separate session
cookies, separate user tables, and — the part that makes the second
sign-in visible rather than instant — **separate Google OAuth clients**
(`556914459776-…` and `666766099662-…`). Google therefore treats them as
two unrelated applications and grants each one separately.

**Wanted:** two repositories, two deployments, one suite. Separate
repositories are not what causes the double sign-in and do not have to
be given up to fix it.

**Three steps, increasing in coupling. The first two are cheap and
independent of the third:**

1. **One OAuth client.** Register both redirect URIs on one client and
   point both services at that secret. Google recognises the existing
   grant, so the second sign-in stops showing consent. One secret
   value, one redirect URI, no code.
2. **Start the flow automatically.** On the directory, an unauthenticated
   request redirects to the provider instead of rendering a sign-in
   page. With step 1 the second sign-in becomes a redirect nobody
   notices. A settings change in that repository.
3. **One session, which means one identity store — and one database.**
   The session cookie carries only `_auth_user_id`; with separate
   `auth_user` tables that number is a different person in each
   application, so sharing the cookie without sharing the table
   authenticates the wrong user.

   Nothing infrastructural is in the way. Both services already run
   against the same Cloud SQL instance,
   `mizzou-news-crawler:us-central1:mizzou-db-prod` — Datadesk on the
   `datadesk` database, the directory on `directory`. Same socket, same
   failure domain. There is no network, IAM or connectivity work.

   The one real constraint is that **Django has no cross-database
   foreign keys**. The directory uses `HistoricalRecords`, so
   `history_user_id` references `auth_user`, and `django_admin_log`
   does too. Those break the moment the user table lives in another
   database. So a shared identity store means a shared *database* —
   separate Postgres schemas within it are fine, separate databases are
   not.

   What it takes:

   - Datadesk owns identity. It already has the role model, the users
     and roles screens, and the audit log; the directory has only
     `is_staff`.
   - Move the directory's tables into the `datadesk` database under
     their own schema. Same instance, so this is a dump and restore
     measured in minutes, not a data migration.
   - The directory stops migrating `auth`, `sessions`, `account` and
     `socialaccount`, and routes them to the shared tables. Migration
     ownership has to be explicit or the two will fight over them.
   - Merge the existing user rows by email. Both sides are Google
     accounts on one hosted domain, so the address is a reliable key
     here in a way it would not be generally.
   - Same `SECRET_KEY`, and
     `SESSION_COOKIE_DOMAIN = ".localnewsimpact.org"`. No load balancer
     — a cookie on the parent domain crosses subdomains for free.
   - Reconcile what a role means. The directory gates its admin on
     `is_staff`; Datadesk gates on groups. After the merge one of them
     governs, and `is_staff` should be derived from the role rather
     than set by hand, or the two will disagree about who is an editor.

   **Do this before item 1, not after.** Item 1 redesigns roles. Doing
   it against two user tables means designing it twice and then
   reconciling; unifying first means designing it once.

   The cost worth naming: one `SECRET_KEY` across both, so a leak is a
   leak of both, and the directory can no longer be brought up without
   the shared database.

**On the subdomain question:** the directory does not need its own
subdomain, but moving it to a path under Datadesk would not deliver
this and would cost more than it appears.

- Cloud Run has no path routing. A shared hostname needs a GCP HTTPS
  load balancer with a URL map.
- Django serves under a prefix with `FORCE_SCRIPT_NAME`, and `reverse()`
  respects it, but static URLs, the allauth callbacks and the OAuth
  redirect URI all need the prefix too.
- The subtle one: on a shared origin both applications set `sessionid`
  and `csrftoken` at `/` and would overwrite each other. Avoiding that
  means scoping the cookies by path, which is precisely what stops them
  being shared. Same-origin makes cookie isolation a thing to manage
  and still does not share the login.

So the subdomain is not the obstacle, and collapsing to a path makes
step 3 harder rather than easier. The subdomain was kept:
`sources.localnewsimpact.org` is its own Cloud Run service on its own
hostname, sharing the session by cookie rather than by origin.

**Touches:** the OAuth client and both services' secrets; allauth
settings in `NewsSourceDirectory`; and, for step 3, whichever service
becomes the identity store, plus item 1, since a role means nothing
until both applications read it from the same place.

## 13. Sign-in for accounts outside the hosted domain

**Now:** the only gate is the Google hosted domain.
`accounts/adapters.py` refuses any login whose email is unverified or
outside `ALLOWED_AUTH_DOMAINS`, so membership of localnewsimpact.org
*is* authorisation. That is simple and it is why there is no invite
flow: everyone who may sign in already has an account by virtue of the
domain.

**Wanted:** an admin creates a person directly, and that person signs in
with whatever Google account they have — a personal Gmail, a university
address, a newsroom's Workspace. Consortium research runs across
institutions; requiring an localnewsimpact.org mailbox to look at the
data does not survive contact with that.

**What changes, and the part to get right:** the gate moves from "which
domain is this" to "is there already an account for this verified
address". The mechanism mostly exists — `SOCIALACCOUNT_EMAIL_AUTHENTICATION`
already attaches a Google identity to an account created ahead of the
first sign-in, which is how an editor can be added before they have ever
logged in. What has to change is the adapter: today an outside address
is refused before that lookup happens.

The risk is that the check inverts from a closed set to an open one. It
must stay closed: **an account must already exist**, created by an
admin, and the address must be one Google has verified. Self-signup
stays off. Getting this wrong turns a domain-gated console into one
anybody with a Gmail can enter, and the failure is silent — it looks
like it works.

**Also needed:** a create-user screen. There is a users list and a role
assignment screen; there is no "add a person" form, because the domain
made one unnecessary.

**One good consequence of item 12:** identity is shared now, so this is
defined once and both consoles inherit it. Before tonight it would have
been two adapters, two user tables and two chances to disagree about who
is allowed in.

**Touches:** `accounts/adapters.py`, `ALLOWED_AUTH_DOMAINS` (which stops
being the authorisation boundary and becomes at most a hint to Google's
account chooser), the users screens, and the directory's
`directory/auth.py`, which carries its own copy of the same rule and
should stop.

**Wants item 1 first**, or close to it: once anyone can be added, which
datasets they may see stops being answerable by "they work here".

## 14. One Django stack, several repositories — done

**Done 2026-08-23.** One image serves both consoles. `SERVICE_ROLE`
selects the front end: unset gives Datadesk, `sources` installs the
`directory` package and serves `datadesk.urls_sources`. Datadesk's
`deploy.yml` builds the image once and rolls out both services, proving
its own before touching the other.

The directory ships as a pip-installable app pinned to a version tag in
`requirements.txt`. A change there reaches production by tagging a
release and bumping that pin — NewsSourceDirectory no longer builds,
deploys or migrates anything.

**Decided 2026-08-23: do this.** Not because two services are painful
today, but because more applications are coming. A shared package per
cross-cutting concern does not scale to N applications; one stack with N
installed apps does. The alternative considered and rejected was
extracting identity alone into a shared distribution — cheaper now,
wrong shape at the third application.

**Shape:** the Source Directory becomes a pip-installable Django app
that Datadesk depends on. Repositories stay separate. Later applications
join the same way.

**One stack is not one deployment.** One image backs several Cloud Run
services, each with its own `ROOT_URLCONF`, which is already how the
directory splits its admin from its public portal (`SERVICE_ROLE`).
Independent hostnames, scaling and the public feed all survive.

**Not `django.contrib.sites`.** That is a table of hostnames and an
integer so one process can tell which domain it serves. It does not
route or isolate. Item 12's `SITE_ID` collision was that feature entire.

### Already compatible — checked, not assumed

- Both run `python:3.14-slim` and Django 5.2.
- No app-label collisions: `accounts audit datasets explorer review
  visuals` against `directory`.
- Dependencies union with no version conflicts. The directory adds
  `import_export`, `simple_history`, `pandas`, `openpyxl`; Datadesk adds
  `bigquery`, `storage`, `ftfy`.
- **No data moves.** Item 12 already put both applications' tables in one
  database under one migration history.

### What collides, and is therefore the work

A first pass compared template *paths* and found one collision. That was
the wrong question: the expensive ones are **scope** collisions, where
one app's override or registration silently applies to a surface the
other owns. Both kinds, now:

1. **One shared `AdminSite` — which is now the intent, not a problem.**
   Datadesk registers 3 models, the directory 11; in one process that is
   one admin containing all 14. **Decided 2026-08-23: every application
   in the suite shares the same admins and roles**, so a single admin
   listing everything is right, and separate `AdminSite` instances would
   be work spent recreating a separation nobody wants. This is also the
   clearest argument for the whole item: one set of administrators
   across N applications is only coherent on one stack.

   **With one qualification.** Roles are granted *per application* —
   editor in one, reviewer in another — so a single admin listing all
   fourteen models cannot show everyone everything. Each `ModelAdmin`
   has to answer `has_module_permission` and `has_view_permission` from
   the grant for *its* application, or the shared admin quietly hands a
   directory reviewer the audit log. One `AdminSite` is still right; it
   is a filtered one. That is item 1's work, and it is why these two
   have to be built together rather than in sequence.
2. **The admin index becomes the suite's, not the directory's.** The
   directory's `admin/base_site.html` and `admin/index.html` override
   globally, and Datadesk serves a stock `/admin/` with no such files —
   so nothing shows as a path collision and its admin would simply
   acquire the LNIC bar and the directory's dashboard tiles. The bar is
   wanted; the tiles are the question. `index.html` counts outlets and
   coverage records, which is a directory dashboard rather than a suite
   one, and it needs to either widen or move behind a heading.
3. **`templates/account/login.html` exists in both** — a genuine path
   collision. In the merged world there is one sign-in for one identity,
   so one of them is deleted rather than namespaced.
4. **Templates are not in the app.** The directory keeps them at the
   repository root and finds them through `TEMPLATES["DIRS"]`, so they
   would not ship inside a package at all. They have to move to
   `directory/templates/` first.
5. **Two `SOCIALACCOUNT_ADAPTER`s** — `directory.auth` and
   `accounts.adapters`, the same rule implemented twice. One process has
   one. Fold together; item 13 rewrites it anyway.
6. **`ROOT_URLCONF` and `LOGIN_REDIRECT_URL`** differ per front end
   (`/` against `/admin/`), so both move into the `SERVICE_ROLE` switch.
7. **Build credentials, and only until launch.** Every LNIC repository
   is open-sourced after launch; `MizzouNewsCrawler` and `news-maps`
   already are. So the privacy this has to work around has an end date,
   and the mechanism should be one that simply loses a step when it
   arrives.

   **Decided: `git+https` against a version tag.**

       news-source-directory @
         git+https://github.com/LocalNewsImpact/NewsSourceDirectory@v0.1.0

   Identical before and after launch — at launch the credential is
   deleted and nothing else changes.

   **Rejected: an Artifact Registry Python repository.** It is the
   better answer if the repos stay private, because Datadesk's
   workflows reference *zero* GitHub secrets today — everything
   authenticates through Workload Identity Federation — and Artifact
   Registry would preserve that. But it is permanent infrastructure
   built for a temporary problem, and the org runs no Python registry
   today, only Docker.

   **Interim credential:** a fine-grained token, read-only, scoped to
   that one repository, with an expiry. It is the first long-lived
   secret in a build that has none. Delete it at launch.

   **Prerequisite: a tagging discipline, which the suite does not yet
   have.** The crawler's `pyproject.toml` declares `version = "1.3.1"`
   and the repository has no tags and no releases; the directory has
   neither. Nothing exists to pin to, and pinning a commit SHA reads
   badly. This is the first dependency in the suite that needs a version
   to point at: bump `version` in the pull request that changes the app,
   tag on merge, pin the tag.

### Suite conventions to match while doing this

Checked against `MizzouNewsCrawler`, which is the reference: these are
all one application suite and should not diverge by accident.

- **Licence: `AGPL-3.0-or-later`.** The crawler declares it in
  `pyproject.toml`. `datadesk` and `NewsSourceDirectory` have no licence
  at all, which has to be settled before either goes public — nobody may
  legally reuse an unlicensed public repository.
- **Project metadata:** `readme`, `authors`, `keywords` and
  `[project.urls]`, as the crawler carries them.
- **`line-length = 88`.** Datadesk and the crawler both use 88; the
  directory uses **100** and is the outlier. Worth its own change rather
  than smuggling a whole-codebase reformat into a packaging pull
  request.

**Two inconsistencies in the crawler**, to fix before publication:

- The crawler's `LICENSE` file is **GPL** v3 while its `pyproject.toml`
  declares **AGPL**-3.0-or-later. Those are different licences.
- Its `[project.urls]` still point at `github.com/your-org/...`.
8. Minor: `directory.views.auth_context` becomes a context processor on
   every render in the process, and the directory's
   `HistoryRequestMiddleware` joins Datadesk's stack. Middleware unions
   cleanly — the directory's list is Datadesk's plus that one entry.

### Sequence

All done, in this order:

1. Move the directory's root `templates/` into `directory/templates/`
   and drop the `DIRS` entry, so they travel with the app.
2. Give NewsSourceDirectory a build system in its `pyproject.toml` —
   it has one already, but only for ruff, pytest and coverage — and
   publish `directory` (plus `checks` and `feed` if either is needed at
   runtime) with its templates as package data.
3. Sort the install credential — a token in the Datadesk build, or an
   Artifact Registry Python repository.
4. Fold the two adapters into one and namespace the sign-in template.
5. Extend `SERVICE_ROLE` to select among the front ends, and give each
   deployment its own value.
6. Add `directory` to Datadesk's `INSTALLED_APPS`; one `migrate` from
   one place.
7. **Delete item 12's scaffolding**, which is the payoff: the router
   guarding against a shadow `auth_user`, `SHARED_IDENTITY`, the
   shared-`SECRET_KEY` arrangement, and NewsSourceDirectory's own deploy.
   A process sharing its own tables is not sharing.

Two items on that list survived rather than being deleted, and the
reasons are worth keeping:

- **The search path stays.** `DB_SEARCH_PATH=directory,public` is how the
  sources front end reaches the `directory` schema. It was listed as
  scaffolding on the assumption that one process makes it unnecessary; it
  does not, because the schema was kept.
- **The split `SITE_ID` stays.** `django_site` maps hostnames to rows,
  and two hostnames cannot share a row. Datadesk owns row 1, the
  directory row 2.

What actually replaced the router is `migrate directory` — naming the app
means the migration cannot create another app's table, which is the whole
job the router was doing. A bare `migrate` under
`search_path=directory,public` would still put an unapplied migration's
table in the wrong schema, so the app name is load-bearing, not stylistic.

Keep the `directory` schema. It costs nothing and moves no rows.

### Done before items 1 and 13, as intended

Two users with matching ids and five migrations at the time; a
people-facing migration once dataset roles and outside accounts exist.
Nothing on the list would have become cheaper by waiting, and it did not
have to.

What items 1 and 13 inherit: one `auth_user`, one set of groups, and one
place to read a role from. Item 1 designs roles once rather than twice.
The one piece of item 12 it also inherits is the `is_staff` question —
the directory gates its admin on `is_staff` and Datadesk gates on groups,
and after the merge one of them has to govern.

## 15. Paywalled sources, and credentials scoped to the person who owns them

**Now:** paywalls are detected and abandoned. `articles.status` carries
`paywall`, enrichment carries `skip_reason="paywall_stub"`, and 968
March articles are teasers or login walls stored as though they were
text. Nothing gets past one.

**Wanted:**

- A source can be marked **paywalled**, per dataset, from the source
  list.
- A review queue task collects credentials for a paywalled source.
- A credential is **usable only by the account that entered it**, so one
  publication may hold several — one per subscriber — and which is used
  depends on who triggered the crawl.

**Why the per-user restriction is a requirement rather than a
preference.** Most publisher terms forbid sharing a subscription, and a
consortium-wide login used by a crawler is the clearest possible breach
of them. Binding a credential to the person whose subscription it is
keeps every authenticated fetch attributable to a subscriber who is
entitled to make it. It is the design that makes the feature defensible,
not a hardening detail to add later.

### Two existing paths would leak these, and neither may be used

- **The audit log stores values verbatim.** `AuditLogEntry.before` and
  `.after` are `JSONField`s holding the old and new value of every
  audited write. A credential written through `audited_update` lands in
  the audit table in plaintext, permanently, by design — the audit log
  is append-only and never edited.
- **The review queue stores values verbatim.** `ChangeProposal`
  carries `current_value`, `proposed_value` and `final_value` as plain
  text, and the queue renders them on screen and into CSV exports.

So credentials never travel through either. The queue task holds a
*reference* — a source, a user, and the id of a secret — and the secret
itself lives in Secret Manager or under a KMS envelope, written once and
never read back into Datadesk. What the queue shows is whether a
credential exists, who owns it, and whether it last worked.

### Decide before building

- **A nightly crawl has no triggering user.** Scheduled runs are the
  normal case and they are exactly what has no `request.user`. Either
  paywalled sources are skipped on unattended runs, or each carries a
  nominated owner whose credential the schedule uses, or a job records a
  "run as" identity. This is the central question: without an answer the
  feature works only for manually triggered crawls.
- **Is `paywalled` a property of the source or of the pair?** A paywall
  belongs to the publication, not to a dataset's view of it. Per dataset
  makes sense if datasets crawl the same publication to different
  depths; otherwise the flag belongs on the source and the checkbox
  merely lives in the dataset's source list.
- **Second factors and CAPTCHAs.** Many paywalls now require one. A
  stored username and password may simply not be able to sign in, and
  no amount of credential plumbing fixes that. Worth testing against
  two or three real targets before building the store.

### Touches

`explorer.models.Source` and `DatasetSource` for the flag; a new
credential-reference model; the review queue's flag vocabulary
(`REVIEW.md`) for "paywalled, no credential" and "credential stopped
working"; the write boundary in `review/services.py`, which must refuse
these fields outright; the export column list, likewise; and the
crawler, which holds the Selenium session that would use them —
`selenium-stealth` is already a dependency there.

**Wants item 1 first.** A credential is the sharpest case of
per-user scoping in the suite, and it should be built on the grant model
rather than inventing a second notion of who may do what.

## 16. Publish visuals statically to news-maps

**Now:** a published visual is served by Datadesk itself.
`datadesk.localnewsimpact.org/embed/<slug>/` and
`/visuals/<slug>/data.json` are public — they return 404 for an unknown
slug rather than redirecting to sign-in — and both are rendered by the
`visuals` app on every request. Versioning already exists:
`VisualSnapshot` holds numbered versions, `publish_visuals refresh`
takes one, and pinning decides which an embed serves, so refreshing data
does not silently change a published embed.

**Wanted:** the pinned snapshot is rendered to static files and served
from `LocalNewsImpact/news-maps`, updated on a cadence, when a visual
changes, and on request from Datadesk.

**Why:** a public embed served by the admin console shares its fate.
A bad deploy or an outage takes down every embed on every site that has
iframed one, and each view costs a Cloud Run request against the same
service that serves authenticated traffic. Static files survive
Datadesk being down, cache at the edge, and cost nothing to serve.

**`news-maps` is currently an empty repository.** It carries the
description "Auto-updating data visuals for LNIC research — static pages
+ nightly data refresh from BigQuery", no files, and no licence. Nothing
in Datadesk references it. It is the intended target and has never been
wired up.

**Not to be confused with `gs://mizzou-news-maps-data`**, which shares
the name and is an *input*: `visuals/services.py` reads a visual's
`bucket_path` from it as a data source. Rendered output has never been
written anywhere.

### What gets published

Per visual, per pinned version: the embed document and its `data.json`.
Both are already the public surface, so the shapes exist; what changes
is where they are served from.

### Decided

- **Served from Firebase Hosting at `maps.localnewsimpact.org`.**
  Automatic managed certificate on a custom domain, a real CDN, and
  `firebase deploy` writes in seconds — which is what the "on request
  from Datadesk" trigger needs. No load balancer.

  The alternatives and why not: **GitHub Pages** is free and serves a
  custom domain, but allows roughly ten builds an hour and queues rather
  than fails beyond that, so a few people clicking republish looks like
  nothing happening. **A public bucket behind Cloud CDN** costs a
  standing ~$18–25 a month for the HTTPS load balancer before a byte
  moves, and a bare bucket cannot serve a custom domain over HTTPS at
  all without one. At consortium traffic that is paying a fixed annual
  fee for what a free tier covers.

- **The embed code UI lives in Datadesk, not on the public site.** The
  people who need it are the ones publishing, and the pinned version is
  already on the visuals index. A public gallery on
  `maps.localnewsimpact.org` would make the full list of published
  visuals public, which is an editorial decision rather than a technical
  one and can be taken later on its own terms.

  `templates/visuals/builder_edit.html` currently hardcodes an embed
  pointing at `datadesk.localnewsimpact.org`, and only builder-template
  visuals show one at all. Once embeds serve from `maps.`, an
  already-copied code still resolves — two live copies drifting apart is
  worse than one that breaks — so this moves with the endpoint rather
  than after it.

### Decide before building

- **The URL, and whether it is versioned in the path.** An embed that
  someone has iframed must keep working forever, so its URL cannot carry
  a version that later moves. A stable path serving the pinned version,
  with versioned paths beside it for citation, is the shape that
  satisfies both — the pin already exists to make that safe.
- **What Datadesk's public routes become.** Once embeds are served
  statically, `/embed/<slug>/` is either the authoring preview, a
  redirect to the static copy, or a fallback. Leaving all three live and
  authoritative is how two copies drift.

### The cadence, and what makes it safe

**Decided:** a nightly refresh runs, and there are three triggers in all
— the nightly run, publishing a change to an existing visual, and
publishing a new one. The last two take effect immediately rather than
waiting for the next night.

A nightly rebuild and a pinned snapshot only conflict if a visual is
vague about the period it covers. **The date range is what separates
them**, and it is why every visual needs one explicitly:

- **A closed range** — "January to June 2026" — is historical. New
  articles arriving tonight do not belong in it, so a refresh produces
  the same data and there is nothing to republish. Most visuals are
  these.
- **An open or relative range** — "the last 30 days", "to date" — is
  current by definition. Refreshing it is the point, so the nightly run
  takes a new snapshot and pins it, because "latest" means the latest.

Without a declared range the two cases are indistinguishable and a
nightly job has to guess: refresh everything and historical charts
silently move, or refresh nothing and current ones go stale.

**`Visual` has no date range today.** It carries `query`, `config` and
`spec`, so a range is either a new field or a required key in `spec`,
and every existing visual needs one before the cadence can run. That is
the first piece of work in this item, ahead of any publishing
machinery.

### Touches

`visuals/services.py` and `publish_visuals`; the existing
`publish.yml` workflow, which already runs on the console's
`repository_dispatch` and is the natural place to add a render-and-deploy
step; a licence and a README on `news-maps`, plus its Firebase config and
a deploy credential; the embed routes in `visuals/urls.py`; and
`templates/visuals/builder_edit.html`, which hardcodes the current host.

**Related:** item 2 pushes the same pinned snapshot to Google Sheets on
publish. Same trigger, different target — they should share one publish
path rather than growing two.

## 17. `random_page_cost` on the shared Cloud SQL instance

**After item 14 — which is done, so this is unblocked.** The merge
changed which process runs these queries and was the larger change; a
planner flag tuned around the old shape would have had to be re-tested
afterwards anyway.

**The observation.** Datadesk's cost rollup joins
`article_enrichment` → `articles` → `candidate_links`. The planner
chooses a nested loop doing **15,759 single-row index lookups at ~0.9ms
each — 8,027ms**. Forcing a hash join with `enable_nestloop = off` runs
the same query in **669ms**.

**Nothing is missing.** The indexes exist and are used —
`articles_pkey`, `candidate_links_pkey` — and the row estimate is close
(6,537 planned against 5,253 actual). The planner is not wrong about the
data; it is wrong about the hardware. `random_page_cost` defaults to 4.0
against an assumption of local disk, and a random read on
network-attached storage costs far more than that relative to a
sequential one. A plan built from many small random reads therefore
looks cheap and is not.

**Why it is not fixed in Datadesk.** It is an instance-level flag on
`mizzou-news-crawler:us-central1:mizzou-db-prod`, which the crawler,
Datadesk and the Source Directory all share. Changing it changes plans
for every query in every one of them — likely for the better, since the
same storage economics apply to all, but that is a claim to test rather
than assume. A flag set for one consumer's dashboard is the wrong way to
tune a shared instance.

**How to test it, rather than flip it:**

1. Collect the slowest real queries from each consumer, not only this
   one. `pg_stat_statements` if it is enabled; otherwise the crawler's
   own known-slow list, which its migrations already document — one
   records a 39.5s query fixed by `ix_article_entities_created_at`.
2. Compare plans and timings at the current 4.0 against candidate
   values, per query, in a session. `SET random_page_cost` is
   session-scoped and needs no instance change to evaluate.
3. Only then decide, recording before and after for each query so a
   regression is attributable to the change.

**Related:** the missing `articles.publish_date` index is a separate and
simpler matter — MizzouNewsCrawler #462, where nothing is tuned, only
added.

**Touches:** nothing in this repository. The change, if made, is a
database flag on the shared instance and belongs with whoever owns it.

## 18. One review pattern across the suite

**Now:** both applications ask a person to judge records, and they ask
in different shapes.

Datadesk's queue (`REVIEW.md`) puts one record on screen with a
controlled flag naming the defect, and three columns that mean one thing
each:

| Proposed change | Current text | Something else |
|---|---|---|
| the value we believe is better | what the record holds now | a field for anything else |
| Accept Proposal · Update it | Keep · No Change | Fix · Use this |

The Source Directory has the same job and different furniture:
`DataQualityIssue` rows carrying a `rule` and a `severity`, and
`needs_review` booleans on `Outlet` and `OutletPlace`, surfaced as
counts on the admin index that link into filtered changelists. A person
lands on a list, opens a record, and edits a form — the defect, the
suggested value and the decision are not on screen together.

**Wanted:** the directory's review uses Datadesk's pattern — a flag from
a controlled vocabulary, the proposed value beside the current one, and
Accept / Keep / Fix with a recorded disposition.

**What carries over unchanged**, because it was learned the hard way and
is written down in `REVIEW.md`:

- The proposed column always holds the **better** value. Offering the
  misspelling under Accept, against a record that is already right, asks
  the reviewer to write the very defect that was flagged.
- Where nothing is known to be better the column is empty and Accept is
  not offered. Keep and Fix remain, which is the honest set.
- A flag names a defect in a record, never the state of a proposed edit.
  "Ready to apply" is not a category a reviewer can act on.
- A decision is durable: it survives a rescan, and the scan does not
  re-ask a question a person has answered while the field still reads
  the way it did.

**What is genuinely different and needs its own answer:** the directory
already has `severity` on its issues, which Datadesk's flags do not
carry. Either severity becomes part of the flag definition — some
defects simply are more serious — or it stays a separate axis and the
queue filters on both.

**Wanted item 14, which is done, so this is unblocked.** On one stack
this is shared code rather than a second implementation of the same
screen: the queue, the flag vocabulary and the disposition model already
exist in `review/` and now serve both
applications' records. Building it twice before the merge would mean merging
two of them afterwards.

## 19. Production: is the pipeline running, and is it healthy

**Now:** nothing in Datadesk says whether the crawler is running. The
corpus dashboard counts what exists; the extraction queue lists what needs
a person. Both describe the result of a pipeline run without describing
the run. Answering "is it working right now" means opening the GCP
console, and answering "did last night go well" means reading logs nobody
has surfaced.

**Wanted:** a **Production** section in the sidebar (item 9's grouped
headers), carrying:

- status of the extraction and processing jobs, per dataset
- live logs of current activity
- warnings and errors picked out of those logs rather than buried in them
- basic statistics for the current run
- how many GKE workers are active
- how many stories sit in each stage of processing, and their disposition

**Where this stops and item 8 starts.** Item 8 is one page per dataset,
answering "how is this dataset doing" — counts by status, health, cost,
the review tasks outstanding. This is the operator's view across all of
them: is anything running, is it failing, how far has it got. They share
their sources and should share their queries; they answer different
questions and belong on different pages. Item 8's "current logs for that
dataset and job" and "workers running" are this item seen through one
dataset, and should be built once here and reused there.

**Who sees it.** Editors and admins. Not viewers, and the same reasoning
as cost visibility: production state is a management fact. The open
question is whether it is a privilege test (`write`, which reviewers also
hold) or a role test (`editor`/`admin`, the set that already gates
imports). Recommend the role test — the reason reviewers are excluded
from imports is that a reviewer's remit is one record at a time, and the
same argument applies to a page about the machinery.

**What exists to build on, and what does not:**

- **Stage counts and dispositions** — the crawler database has them:
  `articles` and `candidate_links` carry status, and Datadesk already
  reads that corpus through `datadesk_ro`. This part needs queries and a
  page, nothing more.
- **Run statistics** — `extraction_telemetry_v2` records per-extraction
  outcomes and is already the source for the extraction views.
- **Live logs** — Cloud Logging, in the `mizzou-news-crawler` project.
  Datadesk's runtime service account has no read access there today. That
  is one IAM grant (`roles/logging.viewer`), and it is the first thing to
  check rather than assume.
- **GKE worker counts** — the cluster is `mizzou-cluster`, also in the
  crawler's project. Reading it needs `roles/container.viewer` on that
  project, and a client: the Kubernetes API rather than a database. This
  is the piece with a real dependency, and the one to scope first, because
  it is the only item on the list that is not a query against something
  Datadesk can already reach.

**Worth knowing before building:** production is stopped and started
deliberately and often. A page that reports a stopped pipeline as a
failure will cry wolf the way the smoke tests did — every liveness figure
on it needs to distinguish "not running because nobody asked it to" from
"not running because something broke". The suspended state of the
cronjobs is the signal that separates them.

**Touches:** the sidebar (`accounts/sections.py`), a new app or a section
of `explorer/`, the crawler read role, and — for logs and workers — IAM in
the crawler's project. Item 1 gates the section; item 8 shares its
queries.

## 20. The visual builder is a form, and it needs to be a tool

**Now:** `builder_edit.html` carries **25 `<select>`, 24 `<input>` and 42
`<label>` across 7 fieldsets** — ninety-one controls on one page, before
any of them is filled in. The preview exists but is subordinate: it sits
beside the form and says "Configure and the preview follows." That
sentence is the design, and it is the wrong way round.

The result is unusable, and not because any single control is wrong. It
is unusable because it asks someone to hold the whole shape of a chart in
their head, express it as ninety-one settings, and only then find out
what they made.

**Wanted:** what Flourish and Datawrapper do. The chart is the page. The
controls are what you reach for when you want to change the thing you are
looking at.

**The UI is the deliverable. Chart coverage is not.** A v1 with six chart
types and an editor someone can use is a success; a v1 with twenty types
behind the current wall is the same failure at greater cost. Everything
below is scoped to that: enough types and dimensions to be genuinely
useful and to exercise the interface across the shapes it has to handle,
and no more.

**Which means one architectural requirement outranks the rest.** Adding a
chart type afterwards must cost a declaration and a renderer — the list
of roles it needs a column for, and the code that draws it — and nothing
else. No new editor screen, no new settings panel written by hand, no
change to the pivot. If adding the eighth type means touching the
builder, the design is wrong and v1 is the moment to find out, not the
twentieth type.

**A v1 set that covers the shapes:**

| Type | Shape it exercises | Available |
|---|---|---|
| Bar | one dimension, one measure | today |
| Stacked bar | two dimensions, one measure | today |
| Table | the pivot's own output, rendered plainly | today |
| Radar | two dimensions again, drawn differently | today |
| Choropleth map | a geographic dimension | today |
| Dot map | points with coordinates | today |

Six types, five distinct shapes, and every one of them runs on
`run_spec()` or `run_story_map()` as they already exist. Nothing on this
list waits for the pivot to change.

Bar, stacked, table and radar deliberately share one shape. That is the
point rather than an accident: they are what proves the type can be
swapped with the data and bindings intact, which is the interaction that
makes the tool feel like a tool. If those four swap cleanly, the design
holds.

**Deferred to v2, with the reason:** chord needs a pair query beside
`run_spec()`, and scatter needs the spec to carry two measures. Both are
contained pieces of work and both are wanted; neither should hold up an
editor, and neither teaches us anything about the interface that the six
above do not.

**A v1 set of channels.** Not all twenty-three dimensions — enough to ask
real questions:

- **Story** — dataset, CIN primary, user need, status, month, year
- **Publisher** — publisher, owner, medium
- **Place** — publisher county, story county, state
- **Measures** — articles, publishers, cost summed

Owner and medium come from the directory and do not exist as dimensions
yet; they are the two most worth adding, because ownership and platform
are the questions this corpus can newly answer since item 14 and the
directory has them clean.

The rest of the twenty-three stay available to the pivot and simply are
not offered in the picker until someone wants them. Adding one is a row.

**The screenshots are examples of a pattern, not a specification.** What
they show is the shape of the task — pick a form, get data into it, see
the result immediately, refine, publish — and the shape is right. The
steps themselves have to be expressed in the data Datadesk actually has,
which is not a spreadsheet someone uploads.

**What a visual here is made from.** `visuals/corpus.py` is already a
pivot over the research corpus: **23 dimensions**, **6 measures**, and
filters. Dataset, publisher, publisher city and county, status, wire,
CIN primary and alternate, month, year, scope, subject, topic, format,
timeframe, user need, model, the skip reasons, and the geographies —
state, county, place, point place. Measured as articles, publishers, cost
summed or averaged, or a confidence average. `Visual.source_kind` also
allows a BigQuery query, a bucket object and uploaded data, but the
corpus is the case that matters and the one the builder exists for.

**So the upload step does not exist for us, and that is the important
difference.** Datawrapper's steps 1 and 2 are "select your map" and "add
your data", because it has no data of its own. Ours already does. Those
two steps collapse into one question with a different shape:

> What am I counting, how am I cutting it, and over which datasets?

A measure, one or two dimensions, and filters. That is the whole of it,
and it is a far smaller thing to ask than "upload a CSV and match its
keys". The author is choosing a slice of something that already exists
and is already correct.

**The steps, in our terms:**

1. **Choose the slice.** Measure, dimension or two, filters, datasets.
   The result is a small table — the pivot already returns a few hundred
   aggregated rows rather than every story, which is what keeps an embed
   from downloading megabytes to draw one map.
2. **See the table, and what it will not draw.** Same place, immediately.
   This is where the coverage report belongs.
3. **Choose a chart type that fits the shape** — and the shape is known,
   because the pivot returned it. One dimension and a measure is a bar or
   a column; a geographic dimension is a map; two dimensions is a stacked
   bar or a heatmap. The gallery can offer the types that *work* for the
   slice and grey the rest, which neither Flourish nor Datawrapper can do
   because neither knows what the data means.
4. **Refine, annotate, lay out.**
5. **Publish and embed.**

**The chart types wanted, and what each needs from the pivot.** These are
examples rather than a closed list, but they are enough to size the work,
and four of the seven need something that does not exist yet.

| Wanted | Shape | Status |
|---|---|---|
| Choropleth map | 1 geographic dimension + 1 measure | **Works today.** `geo_state`, `geo_county`, `geo_place`, `publisher_county` |
| Dot map | points with coordinates | **Works today.** `run_story_map()` already returns `points` and `areas` |
| Stacked chart — CIN by publisher or county | 2 dimensions + 1 measure | **Works today.** `cin_primary` × `publisher` or × `geo_county` |
| Radar — CIN across counties or publishers | 2 dimensions + 1 measure | **Works today.** Same shape as the stacked chart, drawn differently — entity on one axis, CIN values around the other |
| Bar — bylines by platform type | 1 dimension + 1 measure | **Two dimensions missing**, data present |
| Chord — CIN to CIN, or user need to user need | pairs, not groups | **New query shape** |
| Scatter | 1 dimension + **2** measures | **The pivot takes one measure** |
| Table of raw rows | rows, not aggregates | **Conflicts with the design** — see below |

**Bylines and platform type: the columns exist, the dimensions do not.**
`Article.author` is the byline. For platform type there are two candidates
and they are not equal — see below. Adding a dimension is a row each in
`corpus.py`, and it is the cheapest thing on this list.

**Three places hold this data, and since item 14 they are one database.**
The pivot reads the crawler corpus today. It no longer has to.

- **The crawler's articles** — the measures and everything about a story:
  CIN codings, confidence, cost, status, wire, dates, the geographies.
- **The crawler's `sources`** — host, city, county, owner, type. Free
  text, and the older of the two records.
- **The directory's outlets** — 2,809 rows with `medium_id`, `owner_id`,
  `state_id`, city, county, status, founded, closed date, Newsbank
  availability. Normalised, curated, and reviewed. Plus 277 owners,
  231,389 places, and 8,561 coverage records.

Item 10 is the work of making the directory the single source of truth
for a publisher. Charting should assume its outcome rather than encode
the current split: **publisher attributes come from the directory, story
attributes from the crawler.**

**Platform type is the example that proves it.** The directory's
`medium` is a normalised table of six values — Newspaper 1636, Online
449, Radio 262, Television 155, Magazine 52, Public Broadcasting 27 —
reached by a foreign key. The crawler's `sources.type` is free text
holding `digital native` (902), `print native` (148), `newspaper` (20),
`audio_broadcast` (16), `video_broadcast` (15), `broadcast` (10),
`digital_native` (4), and 31 nulls: two spellings of one category, a
`broadcast` that overlaps the two qualified ones, and a different
vocabulary from the directory's entirely.

A bar chart of bylines by platform type should therefore be drawn from
`directory_medium`, and the question of what `sources.type` is *for*
belongs to item 10. Charting from the messy column and cleaning it later
would build the chart twice.

**And it opens dimensions nobody listed.** Ownership is the obvious one:
277 owners, with Adams Publishing Group at 38 outlets, Forum
Communications at 24, Metric Media at 19. Stories per owner, CIN by
owner, or coverage concentration by owner are questions this corpus can
now answer and could not before item 14. Founded and closed dates give a
time dimension on the publisher rather than the story.

**Chord is not a pivot.** A group-by returns counts per category; a chord
diagram needs *edges* — how often CIN primary X co-occurs with alternate
Y, or which user needs appear together. The corpus has the columns for it
(`cin_primary` and `cin_alternate` sit on the same row, so the pair is a
`GROUP BY` over two columns of one article), but it is a different result
shape from everything else here and wants its own function beside
`run_spec()` and `run_story_map()`.

**Scatter needs two measures at once.** `run_spec()` folds a single
`measure_key`. A scatter of counties plotting articles against cost is
one dimension and two measures, which the spec cannot express. It is a
contained change — a list where there is now one key — but it touches the
fold, so it is not free.

**Raw-row tables are a different feature, and the corpus module says so.**
`corpus.py` opens by explaining why it aggregates: fifteen to twenty
thousand articles is a `GROUP BY`, and a published snapshot should hold a
few hundred aggregated rows rather than every story, or each embed
downloads megabytes to draw one map. A table *of the pivot's output* is
free and should exist. A table of raw stories is the explorer, which
already does it, and putting it behind an embed would ship the corpus to
whoever loads the page. Worth deciding deliberately rather than
discovering when a snapshot gets large.

**External data is the exception that needs the matching surface.** Almost
everything charted here comes from our own apps; occasionally something
external is layered in — population, household income — to normalise a
count or provide a denominator. That is the one case with keys that can
fail to match, and it is exactly what Datawrapper's `Match` and
`Check ⚠` tabs are for. So the key-matching design is not wasted, it is
just scoped to the small case rather than being the front door: our data
arrives already joined, and only the layered-in file has to be reconciled
against a geography.

That also decides what the join key is. External sources for places come
keyed by GEOID far more reliably than by name, the corpus already carries
GEOIDs, and the directory holds 231,389 places to resolve against — so a
layered file should be matched on GEOID, with name matching as the
fallback that reports what it could not resolve.

**Coverage replaces key matching, and is a better version of it.**
Datawrapper prefills a table with every county so a missing value is
visible. Our equivalent is stronger, because the corpus knows why a row
is missing rather than only that it is: `corpus.py` already restricts the
county dimension to county, tract and block codings **and says how many
rows that drops**, because a place GEOID is state plus place and contains
no county code. `qualifying_values()` already applies group thresholds.

So step 2 does not ask "did your keys match" — it says "this slice covers
N of M counties, and drops K rows because they are coded to a place that
cannot roll up to a county." That is a sentence no upload-based tool can
write, and it is the one an author here most needs.

**The shape, taken from Flourish (screenshots reviewed 2026-08-23):**

**Two tabs, not one page.** `Preview` and `Data`. Half of the ninety-one
problem is that everything lives on one screen; separating what the chart
looks like from what it is made of halves it before a single control is
redesigned.

**Chart type first, from a gallery — or last, after the data.** Flourish
lets an author start either way, and swap at any point: pick a type and
then bring data to it, or load data and try types against it to see which
works. A `<select>` of names cannot do that, because choosing a chart is
choosing a shape and a list of words asks the author to translate. The
type is named and versioned at the top of the settings rail ("Projection
map, v22.1.0"), so it is always clear what is being edited.

**Swapping the type keeps the data and the bindings.** This is what makes
"try three and see" possible rather than "start again three times", and
it is the requirement that most constrains the design: column bindings
have to be expressed in terms the next chart type can also read.

**Columns are bound to roles, visibly.** The Data tab shows the rows as a
spreadsheet with lettered, type-badged columns, and beside it a short list
of what the chart needs — ID, Name, Label, Colour by, Group, Info for
popups. Each is a coloured chip carrying a column letter, and the same
colour tints that column's header in the grid. So the mapping between
"this column" and "this role in the chart" is visible in both directions
at once. **`Auto set columns`** does the obvious assignment in one click,
which is what makes the first chart appear without configuring anything.

That list — the roles a chart needs — is the honest replacement for most
of the ninety-one dropdowns. It is short because it is per chart type,
and it is what the type declares rather than a union of everything any
type might want.

**Settings grouped by the part of the visual they affect, and collapsed.**
The right rail is an accordion: Projection, Regions layer, Points layer,
Region groups layer, Controls, Popups & panels, Search, Legend, Zoom,
Layout. Grouped by *layer*, not by form-field category, and closed until
opened. Today's seven fieldsets are all open at once, which is why it
reads as a wall.

**The chart stays on screen even in the data view.** A live thumbnail sits
in the corner of the Data tab, so changing a binding shows its effect
without switching back. Nothing an author does should require them to go
and look somewhere else to find out what it did.

**Changing focus is a setting, not a rebuild.** State instead of county,
bar instead of stacked. These are one control on an already-drawn chart,
which only works if the data and bindings survive the change.

**And from Datawrapper (screenshots reviewed 2026-08-23), which solves
the same problem differently:**

**Four numbered steps across the top, each showing whether it is done.**
`1 Select your map ✓` · `2 Add your data ✓` · `3 Visualize` ·
`4 Publish & Embed ✓`. Not a wizard that traps you — every step is
clickable at any time — but the sequence and the state are always on
screen. Step 1 is the chart type, which is the same answer as Flourish's
gallery arrived at from the other direction.

**The keys are known before the data arrives, and the table is
prefilled.** Choosing the Missouri county map fills the table with every
county name, greyed, with `—` for the values. The author types or pastes
values against keys that are already correct. Rows they have data for go
black; the rest stay grey. So "which of these do I have data for" is
answered by looking, before anything is joined.

This is the piece worth taking most. Datadesk's visuals join corpus data
to places, and a name that does not match is the failure that produces an
empty map with no error. A prefilled key list makes the mismatch visible
at the moment it happens rather than at publish.

**`Upload` · `Match` · `Check ⚠`** — three tabs over the data step, and
Check carries a warning badge when something needs attention. Matching
keys is treated as a first-class step with its own surface, not a silent
join that either works or does not.

**Controls show what they do rather than naming it.** The palette is a
gradient swatch, not the word "Blues". `Steps` and `Continuous` are radio
buttons that each render the thing they mean. A pencil beside the palette
opens the editor. Every control carries a `?`. This is the same principle
as choosing a chart type from pictures, applied one level down.

**The title is edited in place.** The chart says `[ Insert title here ]`
and you type into it. It is not a text field in a sidebar that updates
something elsewhere.

**Refine · Annotate · Layout** are the three faces of the visualize step
— what the data looks like, what is written on it, how the whole thing is
arranged. A different cut from Flourish's per-layer accordion, and the
better one for chart types that have no layers.

**Where the two disagree, and what to take from each:**

- **Settings grouping.** Flourish groups by layer (Regions, Points,
  Legend, Zoom), which suits maps and anything with parts. Datawrapper
  groups by intent (Refine, Annotate, Layout), which suits everything.
  Take Datawrapper's cut as the top level, and let a chart type add
  per-layer panels inside Refine when it has layers.
- **Steps versus tabs.** Datawrapper's four numbered steps carry state
  and tell a first-time author where they are. Flourish's two tabs are
  faster once you know the tool. Take the steps: most authors here will
  be occasional.
- **Both keep the chart on screen at every step**, and both make the
  first chart appear before any configuration. Those two are not
  stylistic — they are the difference between a tool and a form.

**What this does not change.** The data model is sound and stays:
`Visual` already separates `source_kind`, `query`, `template`, `config`
and `spec`, and already carries snapshots, pinning and `allow_live`. This
item is about what sits in front of `config` and `spec` — the same fields,
reached by manipulating a chart instead of filling in a form. The embed,
the snapshot model and the publish semantics are unaffected.

**Related, and worth doing together.** Item 3 (a muted chart palette)
changes what the defaults look like, and defaults are most of what an
author sees under this design — a good default chart is the difference
between adjusting and configuring. The palette should be settled before
or alongside this, not after.

**The preview is the embed, decided 2026-08-23.** What the author sees
while editing has to be what the page ships. Not a similar rendering in
the editor and the real one in the iframe — the same thing, so "it looked
right in the editor" cannot happen. That constrains the design rather
than decorating it: every control has to change the visual by changing
what the embed renders from, which means the editor edits `config` and
`spec` and then re-renders exactly as the public route does.

**Nothing to decide about the charting library.** The charts are drawn by
`static/js/datadesk-chart.js` over D3 and Observable Plot, and there are
two renderer templates totalling eighty lines. There is no off-the-shelf
editor to adopt: Flourish and Datawrapper are products rather than
components, and the editor components that do exist — Plotly's, Vega's —
are forms, which is the thing being escaped. The editing layer is ours
either way, and the rendering can stay where it is.

**First step, and it needs no decision:** read the ninety-one controls
against the renderers and separate the ones a chart genuinely needs from
the ones that exist because a form was the only way to offer them. Then
write, per chart type, the short list of roles it needs a column for —
the ID/Name/Colour-by list. Those two lists are what the new design is
built around, and the second is what makes swapping a type possible
without starting again.

**Second step, and the one with the most value per hour:** the coverage
report. For a given slice, say how many of the geography's units it
covers, how many rows are dropped, and why. Most of the arithmetic
exists — `corpus.py` already counts what the county rollup drops, and
`qualifying_values()` already applies the group thresholds. What is
missing is saying it to the author instead of only to the query. It is
useful on its own, before any of the editor is rebuilt, because it turns
a silently empty map into a sentence.

**Touches:** `templates/visuals/builder_edit.html` and
`builder_new.html`, `visuals/builder.py`, the renderers under
`templates/visuals/renderers/`, and the static assets behind them.
`visuals/models.py` should not need to change.

## Sequence

1. **Item 1** first: items 5 and 6 both need dataset-scoped roles, and
   retrofitting them later means touching every view twice.
2. **Item 3** in parallel — it is isolated to the runtime and can land
   any time.
3. **Item 4's first step** (verify the `openrouter_traces` columns) is
   independent and small; the rest of 4 folds into 5.
4. **Item 5** after the allocation-key decision, which is a data
   question about the crawler's workloads, not a Datadesk question.
5. **Item 6** last, as a thin filter over item 1.
6. **Item 2** whenever the tab-overwrite question is settled; it touches
   nothing else.
7. **Item 7** is independent and should start with measurement, not
   code. Worth doing early if the slowness is blocking daily use.
9. **Item 9** is done. The publisher directory it leaves open belongs
   with item 1, which is when the role a link requires stops being a
   two-way question.
8. **Item 8** after items 1 and 6, since the dataset selector and the
   review-task links depend on both — but its aggregates can be built
   and cached before either, and `jobs.params` already carries the
   dataset, so nothing here waits on the crawler.
10. **Item 10's inventory step** can start now and should, because it is
    reading and arguing rather than building, and because every other
    item that touches a publisher record gets easier once the answer
    exists. The rest of 10 waits on that document.
11. **Item 11** after 10's inventory, never before it. It also wants
    item 1 settled, so membership is resolved in one place rather than
    two.
13. **Item 13** after item 1. Adding people from outside the organisation
    and having no per-dataset scoping is the combination to avoid.
16. **Item 16** is independent and can start whenever. Its first step is
    giving every visual a date range; the URL decision comes next and
    cannot be revisited once anyone has embedded a visual.
18. **Item 18** wanted item 14 and now has it: the queue is shared code
    rather than a second implementation of the same screen.
17. **Item 17** wanted item 14 and now has it. The merge changed which
    process issues these queries, so a planner flag tuned to the old
    shape would have needed re-testing regardless.
15. **Item 15** after item 1, and not before the unattended-crawl
    question is answered — a credential feature that only works when a
    person is watching is not the feature.
14. **Item 14 went first**, before items 1 and 13, and is done. It cost
    nothing in data and everything in timing: two users and five
    migrations at the time, a people-facing migration once roles and
    outside accounts exist. Roles and the invited-user model now get
    built once, on one stack, rather than twice across two.
12. **Item 12 is done**, all three steps. The third — one identity store
    — landed before item 1 as intended, so roles are designed once
    against one user table instead of twice against two.
19. **Item 19** after item 1, which gates the section, and alongside
    item 8, which shares its queries. Its worker and log panels want
    two IAM grants in the crawler's project and should be scoped
    before the rest is built — everything else on it is a query
    against a corpus Datadesk already reads.
20. **Item 20** is independent of the access work and can start now.
    Its first step costs nothing and unblocks the rest: audit which of
    the ninety-one controls a renderer actually needs. Settle item 3's
    palette before or alongside it — under this design the default
    chart is most of what an author sees.
