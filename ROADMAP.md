# Roadmap

Work queued after the Phase 0–5 build (SCOPE.md). Each item states what
it changes, what it touches, and what must be decided before it starts.

## 1. Dataset-scoped roles

**Now:** three global groups (viewer/editor/admin). A role grants the
same access to every dataset.

**Wanted:** access is per dataset, and the privilege within a dataset is
one of read / write / design; admin is global.

**Model:**

```
DatasetRole(user, dataset_slug, privilege)   privilege in
                                             {read, write, design}
User.is_admin                                global, sees every dataset
```

- **read** — explorer, dashboards, published visuals for that dataset
- **write** — read plus review, dispositions, imports for that dataset
- **design** — read plus authoring and publishing visuals for it
- **admin** — every dataset, plus user administration and dataset CRUD

**Touches:** `accounts.roles`, every `@role_required` / `@editor_required`
view, the explorer and enrichment grids (filter to permitted datasets),
the review queue (item 6), the cost dashboard (item 5), the visuals
registry (a visual belongs to a dataset), the audit log (unchanged —
still records the actor).

**Decided:** a visual belongs to whichever datasets its author had
access to when building it. The visual records that set, and a viewer
needs access to all of them to see it inside Datadesk. A *published*
visual is public at its embed regardless — publishing is the act that
makes it so, which is why publishing is a privilege (`design`) rather
than a side effect of authoring.

**Still open:**
- Does `design` on one of a visual's datasets suffice to publish it, or
  all of them? All of them is the safer default.
- Do dataset-less pages (the corpus dashboard) show the union of a
  user's datasets, or stay admin-only?

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

**Decide first:**
- Can `openrouter_traces` be attributed to a dataset? Without request
  tagging, billed cost joins only by time, so a per-dataset billed
  figure would be an allocation, not a measurement. Recorded cost joins
  per article and therefore per dataset. This is the crux of item 5.

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

**Consequences worth stating before the labelling starts:**
- A workload serving several datasets at once cannot be labelled for
  one. Either it is split per dataset, or its cost stays in a shared
  bucket the dashboard shows separately — never silently divided.
- Cost before the labelling date is unattributable. The dashboard needs
  a "from" date and should say what predates it.

**Still open:**
- **Which cost lines count as attributable?** Compute and storage
  clearly; the Cloud SQL instance and the load balancer are shared
  overhead, so either excluded or spread by a stated rule.
- Is billing export already enabled to BigQuery, and in which project?

**Touches:** a new `costs` module reading the billing export, the
dashboard, dataset-scoped access from item 1.

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

**Still open:**
- When a fix is a re-extraction rather than a text edit, the resolution
  is asynchronous: the request goes to the crawler and the article
  changes minutes or hours later. Does the item leave the queue on
  request, or stay in a "fix requested" state until the re-extraction
  lands and the condition clears? The second is honest and needs a state
  the first does not.
- Is `review_reason` free text or a controlled vocabulary? Free text
  reads well and aggregates badly; a vocabulary is the opposite. A short
  list per case plus an optional note is the usual compromise, and the
  cases already have their language in `CASE_NOTES`.
- Does a reviewed item resurface when the profile version advances past
  `reviewed_profile_version`? It should be possible; whether it is the
  default is a judgement about how much churn a reviewer will tolerate.
- Does a disagreement feed back to the pipeline automatically (clear the
  skip and let it re-enrich), or only record the judgement? Automatic is
  what a reviewer expects; it also means a wrong click changes the
  corpus, so it wants the audited path and a visible revert.
- Can a reviewer see the whole queue for their dataset, or only items
  assigned to them?
- Pending decisions in the page or as server-side drafts? Drafts let a
  session span pages, which is how the queue gets worked, at the cost of
  a table and a cleanup rule for abandoned sessions.
- One audit entry per submitted session, or one per row? One per session
  keeps the log readable and reverts the batch as a unit; revert already
  restores per row inside it. Per row only helps if reverting a single
  decision from a session is a real need.

**Depends on:** item 1.

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

**Still open:**
- What `logs_path` points at.
- Which worker signal to use.
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

**Still open:** there is no publisher directory — sources are managed
inside dataset detail, and `source_edit` is reachable only from there.
A Sources index belongs in this group when item 1 lands, since who may
see which publishers becomes a dataset-scoped question then.

## 10. One source of truth: merge the crawler's sources into the directory

**Now:** two tables describe the same publishers. The crawler's
`sources` is what the pipeline runs against and what Datadesk reads and
writes through the column boundary. The Source Directory
(`NewsSourceDirectory`, its own repository and database) is the record
of record, with the fields research needs and a public widget over it.
A publisher edited in one is unchanged in the other.

**Wanted:** the directory holds the publisher record; the crawler reads
it rather than keeping its own.

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

**Touches:** `NewsSourceDirectory` (schema and API), the crawler's
source access, and Datadesk's `explorer.models.Source` plus the write
boundary in `review/services.py` — the boundary would move to the
directory's API rather than Postgres column grants, which is a real
change to how the guarantee is enforced and should be decided
deliberately, not inherited.

**Feeds on:** the Missouri Press readings in `data/sources`, which are
already the outside evidence for the fields the directory would own.

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

## 12. One sign-in across the suite

**Now:** signing in to Datadesk does not sign you in to the Source
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
step 3 harder rather than easier. Keep the subdomain; do steps 1 and 2
now, and take step 3 with item 10, which is already deciding where the
publisher record — and therefore who may edit it — lives.

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

## 14. One Django stack, several repositories — decided

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

1. **`templates/account/login.html` exists in both.** The only colliding
   path in either tree. In one process the app that sorts first wins,
   silently.
2. **Two `SOCIALACCOUNT_ADAPTER`s** — `directory.auth` and
   `accounts.adapters`, the same rule implemented twice. One process has
   one. Fold together; item 13 rewrites it anyway.
3. **`ROOT_URLCONF` and `LOGIN_REDIRECT_URL`** differ per front end.
4. **Packaging and build credentials** — a private repository means the
   image build needs a token to `pip install` it.

### Sequence

1. Give NewsSourceDirectory a `pyproject.toml` publishing `directory`
   (plus `checks` and `feed` if either is needed at runtime) with its
   templates and static as package data.
2. Sort the install credential — a token in the Datadesk build, or an
   Artifact Registry Python repository.
3. Fold the two adapters into one and namespace the sign-in template.
4. Extend `SERVICE_ROLE` to select among the front ends, and give each
   deployment its own value.
5. Add `directory` to Datadesk's `INSTALLED_APPS`; one `migrate` from
   one place.
6. **Delete item 12's scaffolding**, which is the payoff: the router
   guarding against a shadow `auth_user`, `SHARED_IDENTITY`, the
   search-path plumbing, the shared-`SECRET_KEY` arrangement, and the
   split `SITE_ID`. A process sharing its own tables is not sharing.

Keep the `directory` schema. It costs nothing and moves no rows.

### Still before items 1 and 13

Two users with matching ids and five migrations today; a people-facing
migration once dataset roles and outside accounts exist. Nothing on this
list makes it cheaper by waiting.

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
14. **Item 14 is decided and goes first**, before items 1 and 13. It
    costs nothing in data and everything in timing: two users and five
    migrations today, a people-facing migration once roles and outside
    accounts exist. Roles and the invited-user model then get built once,
    on one stack, rather than twice across two.
12. **Item 12's first two steps** are independent of everything else and
    can be done in an afternoon. Its third — one identity store — should
    come *before* item 1 rather than after, so roles are designed once
    against one user table instead of twice against two.
