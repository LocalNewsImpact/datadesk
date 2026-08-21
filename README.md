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

## Quickstart

```
make setup    # venv, dependencies, .env from .env.example, migrations
make run      # http://localhost:8000/
make check    # everything CI runs (ruff, black, isort, mypy, pytest)
```

`make superuser` creates an admin login for local development. `make help`
lists all targets.

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

The development database is sqlite. Production is a `datadesk` database
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
