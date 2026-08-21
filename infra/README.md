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
   (SCOPE.md §1). Until the Phase 2 write boundary is decided, this is the
   only path into the crawler's data.
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
| Secrets | `django-secret-key`, `db-password`, `crawler-ro-password`, `google-oauth-client-id`, `google-oauth-client-secret` |
| App database | `datadesk` on `mizzou-news-crawler:us-central1:mizzou-db-prod` |
| Crawler read | role `datadesk_ro`, SELECT-only on `mizzou` |
| Console hostname | `datadesk.localnewsimpact.org` via Cloud Run domain mapping |

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
