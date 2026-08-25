# Infrastructure

Ported from NewsSourceDirectory's infra, which proved every pattern here:
Cloud Run in a dedicated project, database on the crawler's shared Cloud SQL
instance, deploys from GitHub Actions via Workload Identity Federation.

`bootstrap.sh` is the description of the project. Every stage checks before
it creates, so rerunning is safe and reading it tells you what exists.

```bash
./infra/bootstrap.sh                  # everything: project → apis → iam →
                                      #   registry → wif → secrets → sql → data
./infra/bootstrap.sh sql              # just the database and user
./infra/bootstrap.sh domain           # after the first deploy: hostname mapping
```

## First-time order of operations

1. `./infra/bootstrap.sh` — creates `lnic-datadesk`, service accounts, WIF,
   secrets, the `datadesk` database and user on
   `mizzou-news-crawler:us-central1:mizzou-db-prod`, and the read-only
   BigQuery/bucket grants.
2. Set the repository variable it prints (`gh variable set WIF_PROVIDER …`) —
   the Deploy workflow skips quietly until this exists.
3. `./infra/sql/apply.sh isolate_datadesk_role.sql` — closes the two Cloud
   SQL defaults (PUBLIC CONNECT on every database; `cloudsqlsuperuser`
   membership for API-created users). Run with the crawler owners aware — it
   re-asserts grants on their instance.
4. `./infra/sql/apply.sh create_crawler_readonly_role.sql` — creates
   `datadesk_ro`, the SELECT-only role Datadesk reads the corpus through
   (SCOPE.md §1). Then
   `./infra/sql/apply.sh create_crawler_write_role.sql` — creates
   `datadesk_rw`, the audited write role, with the SCOPE.md §6.5
   column-level grants and nothing else.
5. Paste real Google OAuth credentials into the two empty secrets
   (`google-oauth-client-id`, `google-oauth-client-secret`) — sign-in stays
   off, not broken, until then.
6. Push to main (or dispatch the Deploy workflow) — build, migrate, candidate
   revision, health check, traffic shift.
7. `./infra/bootstrap.sh domain` + the Route 53 CNAME it prints.

## Running a management command against production

```bash
./infra/manage.sh <command> [args...]
```

Uses the image the service is currently running, so there is no risk of
executing code that is not deployed.

## What exists (once bootstrapped)

| | |
|---|---|
| Project | `lnic-datadesk`, org `localnewsimpact.org` |
| Region | `us-central1` |
| Registry | `us-central1-docker.pkg.dev/lnic-datadesk/app` |
| Runtime SA | `datadesk-run@` — Cloud SQL client, secret accessor, BigQuery jobs; reader on `mizzou_analytics`, writer on `gs://mizzou-news-maps-data` |
| Deploy SA | `github-deploy@` — Run developer, registry writer, SA user |
| External sign-in | invitation by address, plus the consent screen — `infra/oauth-external.md` |
| Secrets | `django-secret-key`, `db-password`, `crawler-ro-password`, `crawler-rw-password`, `google-oauth-client-id`, `google-oauth-client-secret` |
| App database | `datadesk` on `mizzou-news-crawler:us-central1:mizzou-db-prod` |
| Crawler read | role `datadesk_ro`, SELECT-only on `mizzou` |
| Crawler write | role `datadesk_rw`, column-level UPDATE per SCOPE.md §6.5 |
| Console hostname | `datadesk.localnewsimpact.org` via Cloud Run domain mapping |
| Scheduler | `datadesk-warm-caches` (cache table), `datadesk-keepwarm` and `sources-admin-keepwarm` (cold starts), `datadesk-scan-sources-daily` (review queue — `infra/scan_sources.sh`) |

## Cold starts

Neither console sets `minScale`, so an idle instance is reclaimed and the next
visitor pays for a start. Measured on `sources-admin`, 2026-08-23:

| | |
|---|---|
| Container start → port bound | 2.0s |
| First request after that | 5.4–8.6s |
| Warm | 120–250ms |

The gap is not the container. `gunicorn` runs without `--preload`, so Django
imports on the first request — and Cloud Run's default TCP startup probe
succeeds the moment the port opens, two seconds in, so it routes a real request
into a container that is not ready. Adding `--preload` would make the probe
honest, which matters when scaling up, but does not shorten the wait: Cloud Run
holds the request either way.

What shortens it is not going cold. `infra/keepwarm.sh` creates a Cloud
Scheduler job per service hitting `/_health` every five minutes — Cloud Run
holds an idle instance about fifteen, so that is three chances to miss. It is
inside the free tier. `--min-instances=1` is the guaranteed version at roughly
$7 a month per service.

`/_health` rather than a cheaper path because it queries the database, so the
warm instance also holds a live Cloud SQL connection.

## Public ingress

`--allow-unauthenticated` is required because auth is in-app (allauth Google,
hosted-domain enforced server-side) and `/embed/*` must eventually serve
anonymous readers. The org's Domain Restricted Sharing policy refuses public
invoker bindings unless the project holds an exception on
`constraints/iam.allowedPolicyMemberDomains` — the directory hit exactly
this, and the deploy workflow checks the binding instead of trusting
gcloud's exit code. Request the same project-level exception for
`lnic-datadesk` that `lnic-source-directory` holds.

## The shared instance

Three applications now share `mizzou-db-prod` and its connection cap. The
Django side holds `CONN_MAX_AGE = 60` and Cloud Run runs one gunicorn worker
with 8 threads per instance; raise `--max-instances` deliberately, not by
default, and revisit if the instance shows connection pressure.
