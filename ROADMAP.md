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

**Decide first:**
- Does a visual belong to exactly one dataset, or can it span several?
  Cross-dataset visuals need a rule for who may see them.
- Is `design` a privilege on a dataset or a global capability? A chart
  of two datasets is only publishable by someone with design on both.
- Do dataset-less pages (the corpus dashboard) show the union of a
  user's datasets, or are they admin-only?

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

**Decide first:**
- Push or pull? Push needs a service account with per-sheet sharing and
  the Sheets API enabled; pull needs nothing from us but leaves the
  refresh in the sheet owner's hands.
- Overwrite the tab, or append a dated block? Overwrite keeps one truth;
  append keeps history in the sheet.
- Which artefacts: visuals only, or saved export definitions too?

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

**Decide first — this is the whole difficulty:**
- **What is the allocation key?** Options: by article count per dataset
  in the period; by extraction attempts; by a label on the workload.
  Only a label measures; the others estimate.
- **Can the crawler's GKE workloads carry a dataset label?** If cron
  jobs run per dataset, per-dataset cost is measured directly and this
  becomes reporting rather than modelling. If a single job serves all
  datasets, any per-dataset figure is an apportionment and must be
  labelled as one on the page.
- **Which cost lines count as attributable?** Compute and storage
  clearly; the Cloud SQL instance and the load balancer are shared
  overhead, so either excluded or spread by a stated rule.
- Is billing export already enabled to BigQuery, and in which project?

**Touches:** a new `costs` module reading the billing export, the
dashboard, dataset-scoped access from item 1.

## 6. Review queue scoped to write access

**Now:** the queue shows every dataset to any role that can reach it.

**Wanted:** it lists only datasets where the user holds write.

**Touches:** `review/queue.py`, the queue templates, and the same filter
on the review actions themselves — an item must not be actionable from
a URL a user could otherwise guess.

**Depends on:** item 1. Straightforward once roles are per dataset.

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
6. **Item 2** whenever the push/pull decision is made; it touches
   nothing else.
