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

# The image *and the environment* the console is running, so the job
# scans with the same code and the same databases that serve the queue.
#
# Read back rather than written out here. A hand-listed environment is a
# copy that drifts: the first version of this named five variables of the
# service's sixteen, and two of the secrets by the wrong name, so the job
# started with no crawler database configured, fell back to SQLite and
# died on "no such table: datasets".
DESCRIBE="gcloud run services describe datadesk --project=$PROJECT --region=$REGION"
IMAGE="$($DESCRIBE --format='value(spec.template.spec.containers[0].image)')"
CONFIG="$($DESCRIBE --format=json)"

# Passed through the environment rather than on stdin, so the script
# below can be quoted and read as Python instead of as shell.
read -r ENV_FLAG SECRET_FLAG <<VARS
$(CONFIG="$CONFIG" python3 - <<'READ_ENV'
import json
import os

# Plain values and secret references are two different gcloud flags, and
# a secret read as a plain value would put the literal "projects/..."
# where a password belongs.
container = json.loads(os.environ["CONFIG"])["spec"]["template"]["spec"]["containers"][0]
plain, secret = [], []
for entry in container.get("env", []):
    name = entry["name"]
    if "value" in entry:
        plain.append(f"{name}={entry['value']}")
    else:
        ref = entry["valueFrom"]["secretKeyRef"]
        secret.append(f"{name}={ref['name']}:{ref['key']}")

# Joined with "@", which is the delimiter the flag below declares. A
# comma-joined list under a "^@^" delimiter is one variable whose value
# is every other variable -- which is what happened: fifteen of them
# ended up inside CLOUD_SQL_CONNECTION_NAME. The delimiter exists
# because these values contain commas of their own.
print("@".join(plain), "@".join(secret))
READ_ENV
)
VARS

if [ "${1:-}" = "--run" ]; then
  exec gcloud run jobs execute "$JOB" --project="$PROJECT" --region="$REGION" --wait
fi

echo "job: $JOB   image: $IMAGE"
gcloud run jobs deploy "$JOB" \
  --project="$PROJECT" --region="$REGION" \
  --image="$IMAGE" \
  --service-account="$RUNTIME_SA" \
  --set-cloudsql-instances="$SQL_INSTANCE" \
  --set-env-vars="^@^SERVICE_ROLE=datadesk@${ENV_FLAG}" \
  --set-secrets="^@^$SECRET_FLAG" \
  --task-timeout=30m \
  --max-retries=1 \
  --command="python" \
  --args="manage.py,scan_sources,--if-changed"

# The schedule calls the Run API as this account, so it needs to be
# allowed to run this job. Deploying a job grants nobody anything: the
# job's IAM policy came back empty, `datadesk-run` held only BigQuery,
# Cloud SQL and Secret Manager at the project, and every firing since the
# schedule was made returned PERMISSION_DENIED. The schedule was ENABLED
# the whole time, so nothing looked wrong from the outside -- the scan had
# simply not run since the day somebody ran it by hand.
echo "invoker: $RUNTIME_SA"
gcloud run jobs add-iam-policy-binding "$JOB"   --project="$PROJECT" --region="$REGION"   --member="serviceAccount:$RUNTIME_SA"   --role="roles/run.invoker" >/dev/null

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
