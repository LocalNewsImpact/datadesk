#!/usr/bin/env bash
#
# Keep one instance of each console alive.
#
# Both services run with no `minScale`, so an idle instance is reclaimed and
# the next visitor pays a cold start. Measured on sources-admin 2026-08-23:
# the container binds its port in 2.0s, but the first request took 5.4-8.6s,
# because gunicorn runs without --preload and Django imports lazily on that
# request. Warm, the same service answers /_health in 120-250ms.
#
# Cloud Run keeps an idle instance for roughly fifteen minutes, so a request
# every five keeps one alive with three chances to miss. At ~8,600 requests a
# month of ~200ms each this sits inside the free tier; `--min-instances=1`
# would be the guaranteed version at about $7 a month per service.
#
# /_health is deliberate: it queries the database, so a warm instance also
# holds a live Cloud SQL connection. A path that skipped the database would
# leave the first real visitor paying for the handshake.
#
# Both services allow unauthenticated requests, so no OIDC token is needed.
# The run.app URLs are used rather than the custom domains: fewer layers
# between the scheduler and the container, and warmth is the only goal.
#
# Safe to re-run: each job is updated if it exists and created if it does not.
set -euo pipefail

REGION=us-central1
SCHEDULE="*/5 * * * *"

warm() {
  local job="$1" project="$2" url="$3"

  local action=create
  if gcloud scheduler jobs describe "$job" \
       --location "$REGION" --project "$project" >/dev/null 2>&1; then
    action=update
  fi

  gcloud scheduler jobs "$action" http "$job" \
    --location "$REGION" --project "$project" \
    --schedule "$SCHEDULE" \
    --time-zone "Etc/UTC" \
    --uri "${url}/_health" \
    --http-method GET \
    --attempt-deadline 30s \
    --max-retry-attempts 1 \
    --description "Keeps one instance warm; see infra/keepwarm.sh"

  echo "  ${action}d ${job} -> ${url}/_health"
}

warm datadesk-keepwarm lnic-datadesk \
  https://datadesk-wrm7gxbfsq-uc.a.run.app

warm sources-admin-keepwarm lnic-source-directory \
  https://sources-admin-i6muxr5ina-uc.a.run.app

echo
echo "Scheduler jobs now:"
for p in lnic-datadesk lnic-source-directory; do
  gcloud scheduler jobs list --location "$REGION" --project "$p" \
    --format="value(name,schedule,state)" | sed "s|^|  [$p] |"
done
