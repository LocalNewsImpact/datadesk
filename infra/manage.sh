#!/usr/bin/env bash
#
# Run a Django management command against production.
#
#   ./infra/manage.sh migrate
#   ./infra/manage.sh createsuperuser --noinput --email someone@localnewsimpact.org
#
# It runs inside Cloud Run using the image currently serving, so the code is
# exactly what production is running and nothing needs installing locally.
# Your machine never touches the database — only gcloud credentials are used.
#
# The job is recreated on each run because Cloud Run bakes arguments into the
# job definition rather than accepting them at execution time.
set -euo pipefail

PROJECT="${PROJECT:-lnic-datadesk}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-datadesk}"
JOB="${JOB:-${SERVICE}-manage}"
SQL_INSTANCE="${SQL_INSTANCE:-mizzou-news-crawler:us-central1:mizzou-db-prod}"

[[ $# -gt 0 ]] || { grep '^#' "$0" | sed -n '3,11p' | cut -c3-; exit 1; }

IMAGE="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
          --format='value(spec.template.spec.containers[0].image)')"
[[ -n "$IMAGE" ]] || { echo "could not find the running image for $SERVICE" >&2; exit 1; }

# Cloud Run splits --args on commas by default, and email addresses and URLs
# routinely contain characters that collide with a chosen delimiter. A pipe
# does not appear in anything we pass.
ARGS="manage.py"
for a in "$@"; do ARGS="${ARGS}|${a}"; done

echo "image : ${IMAGE##*/}"
echo "run   : manage.py $*"
echo

gcloud run jobs deploy "$JOB" \
  --image "$IMAGE" --region "$REGION" --project "$PROJECT" \
  --command python --args "^|^${ARGS}" \
  --set-secrets DJANGO_SECRET_KEY=django-secret-key:latest,DB_PASSWORD=db-password:latest \
  --set-env-vars "CLOUD_SQL_CONNECTION_NAME=${SQL_INSTANCE},DB_NAME=datadesk,DB_USER=datadesk" \
  --set-cloudsql-instances "$SQL_INSTANCE" \
  --service-account "datadesk-run@${PROJECT}.iam.gserviceaccount.com" \
  --max-retries 0 --task-timeout 60m --quiet >/dev/null

EXECUTION="$(gcloud run jobs execute "$JOB" --region "$REGION" --project "$PROJECT" \
             --format='value(metadata.name)' --quiet)"
echo "execution: $EXECUTION"

# --wait exists but hides the output, and the output is the entire point.
until [[ "$(gcloud run jobs executions describe "$EXECUTION" --region "$REGION" \
            --project "$PROJECT" --format='value(status.conditions[0].status)')" != "Unknown" ]]; do
  sleep 10
done

echo
echo "---- output ----"
gcloud logging read \
  "resource.type=\"cloud_run_job\" AND labels.\"run.googleapis.com/execution_name\"=\"${EXECUTION}\"" \
  --project "$PROJECT" --limit 200 --format='value(textPayload)' --freshness=1h \
  | grep -v '^$' | tail -r

FAILED="$(gcloud run jobs executions describe "$EXECUTION" --region "$REGION" \
          --project "$PROJECT" --format='value(status.failedCount)')"
[[ -z "$FAILED" || "$FAILED" == "0" ]] || { echo; echo "FAILED"; exit 1; }
