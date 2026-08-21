# Datadesk — scope and delivery plan

A Django application serving two roles over the LNIC research corpus: an
administrative console for the data (review, cleanup, dataset management,
cost insight) and a publishing platform for data visuals embedded in web
reports. It replaces ad-hoc SQL sessions, spreadsheet round-trips, and
paid visualization tooling with one authenticated surface.

## 1. System position

Datadesk owns no research data. It reads from, and writes narrowly back to,
systems that already exist:

| System | Role for Datadesk | Access |
|---|---|---|
| Crawler Cloud SQL (Postgres, `mizzou-news-crawler:us-central1:mizzou-db-prod`) | System of record: articles, enrichment, datasets, sources, gazetteer | Read: dedicated read-only DB role. Write: narrow audited role used only by review/cleanup and dataset-management actions |
| BigQuery `mizzou_analytics` | Analytics mirror (nightly), OpenRouter cost traces (`openrouter_traces` external table over `gs://mizzou-openrouter-logs`) | Read-only service account |
| `gs://mizzou-news-maps-data` (private) | Optional cache for pre-computed visual data | Read/write |
| Datadesk's own Cloud SQL database | Application state only: users, roles, visual registry, review annotations, audit log, import batches | Owner |

Two standing rules inherited from the corpus work:

- **BigQuery is derived.** Every correction lands in the crawler Postgres;
  BigQuery follows on the nightly refresh. Datadesk never writes to BigQuery
  and never treats it as a source for corrections.
- **Cleaned text is protected.** No automated process overwrites
  `author`/`title`/`content`; those columns change only through explicit,
  audited human actions in the review views.

## 2. Feature areas

### 2.1 Accounts and access

`django-allauth` with Google as the sole provider, hosted-domain restricted
(the pattern proven on sources.localnewsimpact.org). Three roles:

- **viewer** — dashboards, data browsing, published visuals
- **editor** — review/cleanup actions, imports, visual authoring
- **admin** — dataset management, user administration, destructive actions

Every mutating action records actor, timestamp, target rows, and
before/after values in an audit table. The audit log is append-only and
visible in the admin.

### 2.2 Data review and cleanup

The March reconciliation, productized. Grid views over articles and
enrichment with the filters that mattered in practice: dataset, status,
wire state, publisher, date range, geography (scope, FIPS, skip reason),
label confidence.

- Inline field edit for byline/headline/text with mojibake detection
  (ftfy preview: show the repaired form before applying)
- Bulk dispositions with recorded reasons (`out_of_scope` +
  `skip_reason`, wire overrides), mirroring the enrichment status machine —
  never inventing statuses the pipeline doesn't know
- Side-by-side article view: stored text, enrichment record (categories,
  confidences, rationales, FIPS claim and mentions), cost
- Every action lands in Postgres through the audited write role and is
  reversible from the audit record

### 2.3 Import and export

- **Import** follows the backpatch protocol proven on the March CSV:
  upload → column mapping → **diff report first** (per-field, with
  mojibake/edit classification) → explicit apply. Imports are batches with
  ids, so an applied batch can be inspected and reverted.
- **Export** produces the deliverable formats already standardized:
  UTF-8 BOM CSVs, one logical row per physical line, article UUID as the
  join key. Saved export definitions (query + columns) can be re-run
  against current data.

### 2.4 Dataset creation and maintenance

CRUD over `datasets`, `dataset_sources`, and `sources` metadata with the
invariants learned this cycle enforced in the forms:

- Source create/edit validates city against the Census place gazetteer
  (catching Grenfield/Kirskville-class typos at entry)
- Dataset membership changes surface their consequences (collection scope
  when crons resume, export eligibility)
- Enrichment profile editor: schema-validated JSON with the version-bump
  reprocessing contract explained inline
- Gazetteer status per source, and a build trigger wired to the offline
  state-extract path (never public Overpass); when a dataset enters a new
  state, the missing Geofabrik extract is flagged
- `default_state` and steady-state floors exposed as first-class fields

### 2.5 Cost insight

Dashboards over two sources joined by time (and by article id once request
tagging lands):

- `article_enrichment.cost_usd` — recorded cost, per dataset / publisher /
  step / day
- `openrouter_traces` — billed cost, cache-hit rate, latency, provider mix

The standing headline: recorded vs billed (the cache discount), run burn
rate against ceilings, and per-article cost distribution. Alerts are out of
scope for v1; the queries exist from the March run and port directly.

### 2.6 Visuals platform

The Flourish-replacement, delivered in two stages.

**v1 — registry and embeds.** A `Visual` model: slug, title, status
(draft/published), data source (a named BigQuery query or a bucket object),
template, and version. Views:

- `/visuals/<slug>/` — the full page
- `/visuals/<slug>/data.json` — the feed (cached; nightly-fresh via the
  BigQuery sync it reads)
- `/embed/<slug>/` — iframe-safe embed with a frame-ancestors allowlist,
  and an embed-code snippet in the admin

Visuals are authored as code (a template plus a data query), registered and
published through the admin. The March story-geography map is the first
resident and the porting exercise that validates the model.

**Embed stability rule:** a published report must not change under its
readers. Publishing a visual pins a data snapshot version; embeds reference
the pinned version by default with an explicit opt-in to live data.

**v2 — form-driven authoring.** The Flourish-like builder: pick a data
source, pick a chart kind (bar, line, table, choropleth+points map), map
columns, preview, publish. Chart rendering standardizes on Observable Plot
(MIT, bundled, no runtime calls to third parties) plus the hand-built map
runtime from v1. v2 is deliberately last: v1 proves the registry, embed,
and snapshot mechanics with real report deadlines before the builder UI is
designed around them.

## 3. Architecture

- Django 5.x, Postgres — a `datadesk` database on the existing
  `mizzou-db-prod` Cloud SQL instance (§6.2), `django-allauth`,
  htmx for grid interactivity before reaching for a SPA
- Read access to the crawler DB through the Cloud SQL connector with a
  read-only role created for the purpose (`--auto-iam-authn` alone fails on
  this instance; password auth via Secret Manager, the proven pattern)
- Cloud Run, following the sources-directory pattern (§6.1):
  GitHub Actions → Workload Identity Federation → build → deploy, the
  two-stage Dockerfile (dependency base rebuilt only on requirements
  change), Cloud SQL over the unix socket, secrets in Secret Manager,
  migrations as a Cloud Run job, domain mapping for
  datadesk.localnewsimpact.org. Long-running work (imports, syncs) runs
  as Cloud Run Jobs on the same image
- No public unauthenticated surface except `/embed/*` and
  `/visuals/*/data.json` for published visuals

## 4. Phases

| Phase | Delivers | Exit test |
|---|---|---|
| 0 | Repo scaffold, auth (Google, domain-restricted, roles), deploy pipeline, read-only connections to crawler DB and BigQuery | A viewer signs in with Google and sees live row counts per dataset |
| 1 | Data explorer (articles + enrichment grids, filters, article detail view) and the cost dashboard | The March repair-queue investigation is reproducible entirely in the UI, no SQL session |
| 2 | Review and cleanup: audited writes, inline edits with ftfy preview, bulk dispositions, import with diff-then-apply, BOM exports | A March-style field backpatch runs end-to-end through the UI with an audit trail and a revert |
| 3 | Visuals v1: registry, page/embed/data views, snapshot pinning, the story-geography map ported and embedded in a test report | The map updates from a nightly sync while a pinned embed stays stable |
| 4 | Dataset management: dataset/source CRUD with validations, profile editor, gazetteer status and build trigger | A new source is added with a typo'd city and the form catches it; a gazetteer build runs from the UI |
| 5 | Visuals v2: form-driven builder over Observable Plot + the map runtime | An editor creates and publishes a bar chart from a saved query without writing code |

## 5. Non-goals

- Replacing the crawler, the enrichment pipeline, or BigQuery — Datadesk is
  a window and a control panel, not a data plane
- Public CMS features (comments, search-engine-facing pages); embeds serve
  reports published elsewhere
- Real-time streaming; nightly-fresh is the contract, matching the
  BigQuery sync cadence

## 6. Phase 0 decisions

Decided 2026-08-21:

1. **Hosting: Cloud Run**, on the sources-directory pattern (which is
   Cloud Run — the deploy workflow, WIF binding, two-stage image,
   `infra/bootstrap.sh`, and runbook port directly). GKE matches the
   crawler, but the crawler is a batch data plane — cronjobs, extraction
   fleets, work queues — which is what GKE is earning its keep for there.
   Datadesk is a request/response console for a handful of internal
   users, structurally the sources directory's twin: Cloud Run scales it
   to zero, makes rollback a traffic change, and keeps a bad deploy out
   of the crawler's cluster. Work that outruns a request becomes a Cloud
   Run Job; the gazetteer build trigger dispatches to the crawler's
   existing offline state-extract path rather than running here.
2. **Database: shared instance.** A `datadesk` database on
   `mizzou-db-prod`, with the sources directory's role-isolation SQL
   ported (`isolate_directory_role.sql` — closes the PUBLIC CONNECT
   default and the `cloudsqlsuperuser` membership Cloud SQL grants every
   API-created user) plus its read-only-role pattern for the crawler
   read role. Connection budget is now shared three ways: `CONN_MAX_AGE`
   and Cloud Run concurrency set deliberately, not defaulted.
3. **Domain: datadesk.localnewsimpact.org**, via Cloud Run domain
   mapping.

Still open:

4. The write-role boundary: exactly which crawler tables accept audited
   writes in Phase 2 (proposed: articles' cleaned-text columns and status,
   article_enrichment disposition columns, datasets/sources metadata)
5. GCP project: a dedicated project (the sources-directory precedent —
   it got `lnic-source-directory` rather than living in
   `mizzou-news-crawler`) vs riding in an existing one. Determines what
   `bootstrap.sh` provisions; needed before the deploy pipeline lands.
