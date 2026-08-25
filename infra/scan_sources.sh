#!/usr/bin/env bash
#
# Scan every directory's publisher records, once a day.
#
# The scan is what puts questions in the review queue, and until this it
# only ran when somebody pressed the button. That made the queue as
# complete as the last person's memory: Missouri's missing owners were
# found and fixed while 894 Vermont publishers with none recorded stayed
# invisible, because nobody had ever typed Vermont's name.
#
# Daily rather than on a trigger, because a publisher record has no
# timestamp to trigger on -- `Source` carries no `updated_at`, and the
# corpus writes to it from the crawler on its own schedule. What makes a
# daily run cheap enough to leave switched on is `--if-changed`: the
# stamp is a hash of every field a scan reads plus the flag vocabulary,
# so a directory whose records have not moved does no work at all and a
# new check re-reads all of them.
#
# Idempotent. Run it again and it makes the same decisions: a proposal
# already queued is refreshed rather than duplicated, and a question a
# person has answered is not asked again (REVIEW.md 4).
#
#     ./infra/scan_sources.sh          # create the job and its schedule
#     ./infra/scan_sources.sh --run    # run it once, now
set -euo pipefail

PROJECT="${PROJECT:-lnic-datadesk}"
REGION="${REGION:-us-central1}"
JOB="datadesk-scan-sources"
SCHEDULE_NAME="datadesk-scan-sources-daily"
# 09:10 UTC — a few minutes past the hour so it does not land with every
# other cron on the hour, and overnight in Missouri so the queue is ready
# when somebody sits down to it.
SCHEDULE="${SCHEDULE:-10 9 * * *}"
RUNTIME_SA="datadesk-run@${PROJECT}.iam.gserviceaccount.com"
SQL_INSTANCE="mizzou-news-crawler:us-central1:mizzou-db-prod"

# The image the console is running, so the job scans with the same code
# that serves the queue. Reading it back rather than naming a tag: a job
# pinned to `latest` drifts from the service without either changing.
IMAGE="$(gcloud run services describe datadesk \
  --project="$PROJECT" --region="$REGION" \
  --format='value(spec.template.spec.containers[0].image)')"

if [ "${1:-}" = "--run" ]; then
  exec gcloud run jobs execute "$JOB" --project="$PROJECT" --region="$REGION" --wait
fi

echo "job: $JOB   image: $IMAGE"
gcloud run jobs deploy "$JOB" \
  --project="$PROJECT" --region="$REGION" \
  --image="$IMAGE" \
  --service-account="$RUNTIME_SA" \
  --set-cloudsql-instances="$SQL_INSTANCE" \
  --set-env-vars="SERVICE_ROLE=datadesk" \
  --set-secrets="DJANGO_SECRET_KEY=django-secret-key:latest,DB_PASSWORD=db-password:latest,CRAWLER_RO_PASSWORD=crawler-ro-password:latest,CRAWLER_RW_PASSWORD=crawler-rw-password:latest" \
  --task-timeout=30m \
  --max-retries=1 \
  --command="python" \
  --args="manage.py,scan_sources,--if-changed"

echo "schedule: $SCHEDULE_NAME ($SCHEDULE)"
gcloud scheduler jobs create http "$SCHEDULE_NAME" \
  --project="$PROJECT" --location="$REGION" \
  --schedule="$SCHEDULE" --time-zone="Etc/UTC" \
  --uri="https://run.googleapis.com/v2/projects/${PROJECT}/locations/${REGION}/jobs/${JOB}:run" \
  --http-method=POST \
  --oauth-service-account-email="$RUNTIME_SA" \
  2>/dev/null || gcloud scheduler jobs update http "$SCHEDULE_NAME" \
  --project="$PROJECT" --location="$REGION" \
  --schedule="$SCHEDULE" --time-zone="Etc/UTC" \
  --uri="https://run.googleapis.com/v2/projects/${PROJECT}/locations/${REGION}/jobs/${JOB}:run" \
  --http-method=POST \
  --oauth-service-account-email="$RUNTIME_SA"

echo "done. one run now:  ./infra/scan_sources.sh --run"
