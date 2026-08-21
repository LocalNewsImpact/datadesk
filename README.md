# Datadesk

Django console for LNIC research data: review and cleanup, dataset
management, cost insight, and a publishing platform for embeddable visuals.

See [SCOPE.md](SCOPE.md) for the delivery plan. The current state is the
Phase 0 scaffold: project layout, Google-only auth with hosted-domain
restriction, role groups, the append-only audit log, and CI. Hosting is
decided (SCOPE.md §6: Cloud Run on the sources-directory pattern, a
`datadesk` database on the shared Cloud SQL instance,
datadesk.localnewsimpact.org, a dedicated GCP project); the deploy
pipeline and the read-only crawler-DB/BigQuery connections are the next
work.

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
