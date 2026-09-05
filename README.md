# Datadesk

Django console for LNIC research data: review and cleanup, dataset
management, cost insight, and a publishing platform for embeddable visuals.

See [SCOPE.md](SCOPE.md) for the delivery plan. Phase 0 is code-complete
(auth, roles, audit log, deploy pipeline — see
[infra/README.md](infra/README.md) — and the read-only crawler-DB and
BigQuery connections; what remains is running the bootstrap against GCP
and the first deploy). Phase 1, the data explorer, is in: the articles
grid with the March filters, the enrichment grid with the geography
filters, the side-by-side article detail, and the recorded-vs-billed
cost dashboard, all read-only over the `datadesk_ro` role and
htmx-enhanced. The billed-cost BigQuery query needs truing up against
the real `openrouter_traces` columns on first live run
(explorer/costs.py).

Phase 2's audited write path is in: inline cleaned-text edits with the
ftfy mojibake preview (diff shown, applied only on explicit choice),
bulk dispositions with recorded reasons, and revert from the audit
record. Writes flow through the `datadesk_rw` role, whose column-level
grants (SCOPE.md §6.5) are the boundary — created by
`infra/sql/create_crawler_write_role.sql` — and are auth-gated to the
editor and admin roles. Import follows the proven backpatch protocol
(upload → column mapping → diff report with mojibake/edit
classification → explicit apply, batches revertible as a unit), and
exports produce the standardized deliverables (UTF-8 BOM CSV, one
logical row per physical line, article UUID join key) with saved,
re-runnable definitions. Phase 2 is code-complete.

Phase 3, visuals v1, is code-complete: the registry (a `Visual` is a
renderer template in the repo plus a BigQuery query or bucket object,
registered and published through the admin), `/visuals/<slug>/`,
`/visuals/<slug>/data.json`, and `/embed/<slug>/` with a per-visual
frame-ancestors allowlist — the embed and feed being the only public
routes, for published visuals only. Publishing pins a data snapshot;
embeds serve the pin (`?live=1` works only where a visual opts in), so
a published report never changes under its readers. The March
story-geography map becomes the first registration once its assets
(`gs://mizzou-news-maps-data`) are reachable from a deployed
environment.

Phase 4, dataset management, is code-complete: dataset CRUD (creation
starts with cron off), membership add/remove with consequences noted
and full revertibility, `default_state` as a first-class field, the
enrichment-profile editor validating against the pipeline's schema and
enforcing the version-bump reprocessing contract, source create/edit
with the city validated against the vendored Census place gazetteer
(typos refused with suggestions), per-source gazetteer status, and a
build-request queue that records the exact `populate-gazetteer` command
— dispatch to the crawler's job infrastructure is the remaining wiring.
The Phase 4 write grants (source/dataset INSERT, membership
INSERT/DELETE) are in `create_crawler_write_role.sql`, which is
idempotent — rerun it if the role predates them.

Phase 5, the form-driven builder, is code-complete: editors create a
visual from an uploaded CSV, a BigQuery query, or a bucket object, pick
a chart kind — bar, line, area, scatter, donut, chord, arc diagram,
table, and the GIS pair: choropleth and point maps at every level from
nation to census tract (nation/state/county boundaries ship with the
repo; place and tract boundaries load per state from the joined GEOIDs,
built and committed by `infra/fetch_boundaries.sh` — MO, PA, and MN are
in) with FIPS joins, sequential or diverging ramps, zoom-to-data, and
size/label point encodings — map columns in a live preview, and
publish; the embed, pinning, and feed are the unchanged v1
machinery. Rendering is the vendored Observable Plot + d3 +
topojson-client with Census TIGER boundary files — no runtime calls to
third parties — driven by a runtime that encodes validated
accessible palettes (light and dark), fixed-order series assignment
with fold-to-Other caps, hover tips, legends, and an always-available
data table view. Four brand themes ship, each run through the palette
validator in both modes: LNIC (the default — localnewsimpact.org
blues), Mizzou (MU gold and crimson, stepped chart-safe), RJI (steel
blue with the affiliation gold), and a neutral reference.

## The suite

Datadesk is one of four repositories that share a database, a contracts
package, a deployment project and one set of rules about how a change
reaches `main`. It is the reading and deciding end: the crawler writes
the rows, this console is where a person looks at them and records what
should happen.

### The repositories

| Repository | What it is | What it publishes |
| --- | --- | --- |
| `MizzouNewsCrawler` | discovery, extraction, cleaning, classification and enrichment; GKE + Argo Workflows | rows in the crawler database; analytics tables in BigQuery |
| `datadesk` | the newsroom console and review queue; Django on Cloud Run | decisions written back to the crawler database; published visuals |
| `NewsSourceDirectory` | the outlet directory and its review queue; Django on Cloud Run at `sources.localnewsimpact.org` | a hashed static feed on `gh-pages`, read by the WordPress plugin |
| `lnic-contracts` | the shapes one service writes and another reads, the shared CI, the coverage floor | a Python package and two tag series |

### How main is protected

Two layers, deliberately split.

**One organization ruleset**, `Main is reached by pull request`, targets `~ALL` repositories' default branch:

| Rule | Effect |
| --- | --- |
| `pull_request` | a change reaches main through a pull request; one approving review, code-owner review where a CODEOWNERS file matches |
| `non_fast_forward` | no force-push over main |
| `deletion` | main cannot be deleted |

**Each repository's own ruleset** carries `required_status_checks` and nothing else. That rule cannot move up to the organization: the contexts differ per repository — `checks / integration` exists only in the crawler, `Data quality`, `Public feed`, `Pages payload` and `Image builds` only in the Source Directory — and a context named in a ruleset but never reported blocks every pull request permanently.

The rules the suite works to:

1. Nothing is pushed to origin except on a branch.
2. Every repository has a pre-push hook that runs `make check`, so what CI will say is known before it is said.
3. CI checks the pull request, and green is what allows a merge.
4. An administrator may merge without a code review.
5. Nobody pushes to main, administrators included.

Four and five look contradictory. The ruleset's bypass list resolves them, and the mode is what does it: `OrganizationAdmin` bypasses in `pull_request` mode, which permits an override **while merging a pull request** and none at all for a direct push.

| Bypass mode | Direct push to main | Merge against the rules |
| --- | --- | --- |
| `always` | allowed | allowed |
| `pull_request` (in use) | refused | allowed, with `--admin` |

So an administrator merges with `gh pr merge <n> --squash --admin`, and a merge without `--admin` waits for a review. The GraphQL field `viewerCanMergeAsAdmin` reports `false` under this configuration and the `--admin` merge succeeds anyway; it describes the legacy branch-protection override, not a ruleset bypass, and is not the field to read.

`delete_branch_on_merge` is on in every repository.

### What enforces what

| Layer | Catches | Where it lives |
| --- | --- | --- |
| pre-push hook | a red commit, before it leaves the machine | `scripts/setup-hooks.sh`, one per repository, running that repository's `make check` |
| shared CI | a red pull request | `lnic-contracts/.github/workflows/python-checks.yml@ci-v1` — lint, typecheck, test, integration, with a Postgres service |
| `conforms.yml` | a repository drifting from the pattern | `lnic-contracts`, called alongside the checks |
| the ruleset | a merge that skipped either | GitHub, organization and repository level |

`conforms.yml` fails a repository that stops calling the shared workflow, loses a make target the workflow runs, drops its pre-push hook, lets that hook run the whole suite for a branch deletion, leaves CI's push trigger unscoped so every pull request push runs twice, sets a coverage floor of its own, or stops running the suite's floor from `make test`.

Every stage is a make target — `make lint`, `make test` — never a bare `ruff` or `pytest`. The commands live in each repository's Makefile, which is what a person runs locally, so CI and a local run cannot mean different things. What the targets *do* differs: the crawler runs its tests inside a prebuilt image because its dependencies take minutes to install; the others install them on the runner because they take seconds. Both are `make test`.

The coverage floor is one number, 80%, in `lnic_contracts.coverage_floor`, run by every repository's `make test` and again by the shared workflow. A repository that sets its own is refused.

### How the repositories are joined

**Data.** One Cloud SQL instance serves all three applications. The crawler owns its database; the Source Directory's tables live in a `directory` schema alongside shared identity tables in `public`; datadesk has its own database and reaches the crawler's through a **read-only role** (`infra/sql/create_crawler_readonly_role.sql`, password in Secret Manager), with a separate read-write connection for the decisions the review queue writes back. Postgres enforces the read-only half; it is not a convention.

**Packages.** `lnic-contracts` is installed from a tag tarball, pinned in each consumer's requirements. `NewsSourceDirectory` is installed into datadesk's base image from a pinned git tag, so the directory front end datadesk serves is a released version rather than whatever `main` happens to be; tagging a directory release dispatches datadesk's deploy.

**Versioning.** `lnic-contracts` carries two tag series, because the cadences differ. `vX.Y.Z` versions the Python package — the shapes two services must agree on, where a renamed key strands data at runtime with no import error to catch it. `ci-vX.Y.Z` versions the workflows, and `ci-v1` follows the newest of them, so a CI fix reaches all three repositories without a pull request in each. `release-ci.yml` runs `make check` before moving the major tag.

**Publishing.** The crawler exports to BigQuery. The Source Directory publishes a hashed static feed to `gh-pages`, which the WordPress plugin reads. datadesk publishes visuals.

### This repository's place in it

Datadesk holds no article data of its own. Its Django database is
`datadesk` on the shared instance; the articles, enrichment and dataset
tables it reads belong to the crawler and are reached through the
read-only `datadesk_ro` role, with `datadesk_rw` and its column-level
grants carrying the review queue's decisions back. The boundary is
Postgres grants, not application code.

The Source Directory is installed into this repository's base image
from a pinned git tag, so the directory front end served here is a
released version rather than whatever `main` holds; a directory release
dispatches this repository's deploy workflow. The review note two
services must agree on comes from `lnic-contracts` -- a key renamed on
one side strands a held article with no import error to catch it, which
is the failure that package exists to prevent.

Required checks here are `checks / lint`, `checks / test` and
`conforms / conforms`. mypy runs inside `make lint` rather than as its
own stage, and there is no integration stage, so the shared workflow is
called with those two flags off.

Every push to `main` deploys. Work on a branch.

---

## Quickstart

```
make setup    # venv, dependencies, .env from .env.example, migrations
make run      # http://localhost:8000/
make check    # everything CI runs (ruff, black, isort, mypy, pytest)
```

`make superuser` creates an admin login for local development. `make help`
lists all targets.

The test suite runs on Postgres, because production does — `make check`
and `make test` start it (`docker-compose.test.yml`, port 5435) and stop
nothing, so a second run reuses it; `make test-db-down` stops it. Running
`pytest` with no database is refused rather than allowed to fall back:
sqlite accepts SQL Postgres refuses, and defects have reached production
through a green sqlite run.

The development *server* still uses sqlite. Only the suite requires
Postgres.

Two checks need a connection to the crawler's real database (the Cloud
SQL Auth Proxy locally), so they are commands rather than tests:

```
make crawler-schema   # do the unmanaged models still match the crawler?
make smoke-queries    # do the console's read paths actually run?
```

`check_crawler_schema` is the answer to a schema this repository does not
own and is not told about: a column renamed or retyped in the crawler
leaves the suite green and breaks a page. `smoke_queries` runs the
expensive reads against the real databases — the deploy runs it as a job
on the candidate revision before traffic shifts, so a query that cannot
run holds the rollout instead of reaching the site. `/_health` renders
without touching the crawler and proves neither.

## Configuration

All deployment-specific values come from environment variables
(`.env` is sourced by the Makefile targets for local development):

| Variable | Purpose | Local default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Session/signing key; required in production | insecure dev value |
| `DJANGO_DEBUG` | `1`/`true` enables debug | off |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts | `localhost,127.0.0.1` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated origins | empty |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | Google sign-in credentials; blank leaves the provider unconfigured and the admin login available | blank |
| `ALLOWED_AUTH_DOMAINS` | Comma-separated Google hosted domains allowed to sign in; empty disables the restriction (development only) | empty |
| `DATADESK_SQLITE_PATH` | Development sqlite location | `./db.sqlite3` |

The development database is sqlite (the test suite is not — see
Quickstart). Production is a `datadesk` database
on the shared Cloud SQL instance, reached over the Cloud Run unix socket
with credentials from Secret Manager (the sources-directory pattern —
SCOPE.md §6.2). The seam is a commented block in `datadesk/settings.py`,
activated when the deploy pipeline lands.

## Access model

Google via django-allauth is the sole sign-in path (SCOPE.md §2.1); local
password signup is closed. The hosted-domain claim is enforced in
`accounts/adapters.py` — the `hd` OAuth parameter is only a hint to
Google's account chooser. Roles are the Django groups `viewer`, `editor`,
and `admin`, created by the accounts data migration; new sign-ins have no
role until one is assigned in the admin.

Every mutating action will be recorded in the append-only audit log
(`audit.AuditLogEntry`), visible read-only in the Django admin.
