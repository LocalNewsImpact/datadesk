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

**Wanted, two parts:**

1. The queue lists only datasets the user may act on.
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

**Still open:**
- Can a reviewer see the whole queue for their dataset, or only items
  assigned to them?
- Does accepting a batch of proposals produce one audit entry or one per
  proposal? One per proposal keeps revert granular.

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
