#!/usr/bin/env bash
#
# Make the records agree with what reviewers said about them, once a day.
#
# The console does not do the work: the crawler and the processor do. This
# only asks for it. A reconciliation sets statuses -- the gates every
# pipeline stage selects on -- and the crawler's own housekeeping
# workflow, at 03:00 UTC, then carries whatever is ready. Nothing here
# fetches, classifies or enriches anything.
#
# 02:30 UTC, so the request is in before the work runs, and both are well
# ahead of the 07:00 BigQuery sync -- a story retracted overnight stops
# being published the same morning.
#
# What it does is written down in MizzouNewsCrawler/docs/
# QUEUE_RECONCILIATION.md, and the state machine it routes against in
# docs/PIPELINE_STATES.md. Read those before changing the schedule: the
# first run moves 545 records and takes 161 articles out of BigQuery.
#
# --apply is passed here deliberately. The command reports and writes
# nothing without it, which is right for a person at a terminal and wrong
# for a schedule that exists to keep the records true.
#
# The release re-pins the job to each new image
# (gcp/cloudbuild/cloudbuild-datadesk.yaml). A job runs the image it was
# deployed with and nothing about it follows the service, so without that
# it goes on running the build it was made with -- and a rule added
# afterwards never runs, however many mornings the schedule fires.
#
#     ./infra/reconcile_queues.sh          # create the job and its schedule
#     ./infra/reconcile_queues.sh --run    # run it once, now
set -euo pipefail

PROJECT="${PROJECT:-lnic-datadesk}"
REGION="${REGION:-us-central1}"
JOB="datadesk-reconcile-queues"
SCHEDULE_NAME="datadesk-reconcile-queues-daily"
# 02:30 UTC, before the crawler's 03:00 housekeeping picks up what this
# sets, and before the 07:00 BigQuery sync reads the result.
SCHEDULE="${SCHEDULE:-30 2 * * *}"
RUNTIME_SA="datadesk-run@${PROJECT}.iam.gserviceaccount.com"
SQL_INSTANCE="mizzou-news-crawler:us-central1:mizzou-db-prod"
# Every write goes through the audited path, which records who made it.
# A schedule has no person behind it, so it writes as the account that
# owns the console's automated work rather than as whoever last logged in.
ACTOR="${ACTOR:-datadesk-run@${PROJECT}.iam.gserviceaccount.com}"

# The image *and the environment* the console is running, so the job
# reconciles with the same code and the same databases that serve the queue.
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
  --args="manage.py,reconcile_queues,--apply,--actor,${ACTOR}"

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

echo "done. one run now:  ./infra/reconcile_queues.sh --run"
