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

**Approach, cheapest first:**

1. **Measure before changing anything.** Which pages, and is the time in
   the query, the render, or the payload? The corpus is 164k articles
   across a shared Cloud SQL instance; a missing index and a slow
   template look identical from a stopwatch.
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
8. **Item 8** after items 1 and 6, since the dataset selector and the
   review-task links depend on both — but its aggregates can be built
   and cached before either, and `jobs.params` already carries the
   dataset, so nothing here waits on the crawler.
