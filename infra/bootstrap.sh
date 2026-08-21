#!/usr/bin/env bash
#
# Bootstrap the GCP project for Datadesk.
#
# Ported from NewsSourceDirectory/infra/bootstrap.sh, which proved the shape.
# Every stage is idempotent: it checks for the resource before creating it, so
# rerunning is safe and the script doubles as the description of what exists.
#
#   ./infra/bootstrap.sh            # everything except the database
#   ./infra/bootstrap.sh sql        # the database and user on the shared instance
#   ./infra/bootstrap.sh apis iam   # named stages, in order
#
# Requires an account with resourcemanager.projectCreator and billing.user on
# the org, plus rights on mizzou-news-crawler for the sql and data stages.
# See infra/README.md.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-lnic-datadesk}"
PROJECT_NAME="LNIC Datadesk"
ORG_ID="${ORG_ID:-293319414046}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:-011142-05FA4C-0FCA10}"
REGION="${REGION:-us-central1}"

# The database lives on the crawler's existing instance rather than a new one
# (SCOPE.md §6.2). A dedicated instance would be ~$50/month; this is $0, and
# the instance already carries the crawler's and the directory's databases.
SQL_PROJECT="${SQL_PROJECT:-mizzou-news-crawler}"
SQL_INSTANCE="${SQL_INSTANCE:-mizzou-db-prod}"
DB_NAME="${DB_NAME:-datadesk}"
DB_USER="${DB_USER:-datadesk}"
REPO="${REPO:-app}"

# Read-only sources Datadesk consumes (SCOPE.md §1).
BQ_DATASET="${BQ_DATASET:-mizzou_analytics}"        # in ${SQL_PROJECT}
MAPS_BUCKET="${MAPS_BUCKET:-mizzou-news-maps-data}" # optional visuals cache

# The GitHub repository allowed to deploy, via Workload Identity Federation.
GITHUB_REPO="${GITHUB_REPO:-LocalNewsImpact/datadesk}"

# Hostname for the console. DNS is Route 53; a record for this exact name
# overrides the existing *.localnewsimpact.org wildcard.
ADMIN_HOST="${ADMIN_HOST:-datadesk.localnewsimpact.org}"
SERVICE="${SERVICE:-datadesk}"

RUN_SA="datadesk-run@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOY_SA="github-deploy@${PROJECT_ID}.iam.gserviceaccount.com"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
have() { gcloud "$@" >/dev/null 2>&1; }
gc() { gcloud --project="$PROJECT_ID" "$@"; }

# --------------------------------------------------------------------------

stage_project() {
  say "project ${PROJECT_ID}"
  if have projects describe "$PROJECT_ID"; then
    echo "  exists"
  else
    gcloud projects create "$PROJECT_ID" \
      --organization="$ORG_ID" --name="$PROJECT_NAME"
  fi

  local linked
  linked="$(gcloud billing projects describe "$PROJECT_ID" \
    --format='value(billingEnabled)' 2>/dev/null || echo False)"
  if [[ "$linked" == "True" ]]; then
    echo "  billing already linked"
  else
    gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
  fi
}

stage_apis() {
  say "APIs"
  gc services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    storage.googleapis.com \
    iamcredentials.googleapis.com \
    bigquery.googleapis.com
}

stage_iam() {
  say "service accounts"
  for pair in "datadesk-run:Cloud Run runtime" "github-deploy:GitHub Actions deployer"; do
    local id="${pair%%:*}" desc="${pair#*:}"
    if have iam service-accounts describe "${id}@${PROJECT_ID}.iam.gserviceaccount.com" \
        --project="$PROJECT_ID"; then
      echo "  ${id} exists"
    else
      gc iam service-accounts create "$id" --display-name="$desc"
    fi
  done

  # Runtime: reach the database, read its secrets, run BigQuery queries
  # (billed to this project; the data lives in the crawler's). Nothing else.
  for role in roles/cloudsql.client roles/secretmanager.secretAccessor roles/bigquery.jobUser; do
    gc projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${RUN_SA}" --role="$role" --condition=None >/dev/null
  done

  # Deployer: ship images and revisions, act as the runtime SA. No data access.
  for role in roles/run.developer roles/artifactregistry.writer roles/iam.serviceAccountUser; do
    gc projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${DEPLOY_SA}" --role="$role" --condition=None >/dev/null
  done
  echo "  roles bound"
}

stage_registry() {
  say "artifact registry"
  if have artifacts repositories describe "$REPO" \
      --location="$REGION" --project="$PROJECT_ID"; then
    echo "  exists"
  else
    gc artifacts repositories create "$REPO" \
      --repository-format=docker --location="$REGION" \
      --description="Datadesk images"
  fi
}

stage_wif() {
  say "workload identity federation for ${GITHUB_REPO}"
  local pool=github provider=github
  if have iam workload-identity-pools describe "$pool" \
      --location=global --project="$PROJECT_ID"; then
    echo "  pool exists"
  else
    gc iam workload-identity-pools create "$pool" \
      --location=global --display-name="GitHub Actions"
  fi

  if have iam workload-identity-pools providers describe "$provider" \
      --workload-identity-pool="$pool" --location=global --project="$PROJECT_ID"; then
    echo "  provider exists"
  else
    gc iam workload-identity-pools providers create-oidc "$provider" \
      --location=global --workload-identity-pool="$pool" \
      --issuer-uri="https://token.actions.githubusercontent.com" \
      --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
      --attribute-condition="assertion.repository=='${GITHUB_REPO}'"
  fi

  local num; num="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
  gc iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
    --role=roles/iam.workloadIdentityUser \
    --member="principalSet://iam.googleapis.com/projects/${num}/locations/global/workloadIdentityPools/${pool}/attribute.repository/${GITHUB_REPO}" \
    >/dev/null
  echo "  repo may impersonate ${DEPLOY_SA}"
  echo
  echo "  deploy.yml reads the provider from a repository variable. Set it once:"
  echo "    gh variable set WIF_PROVIDER --repo ${GITHUB_REPO} \\"
  echo "      --body 'projects/${num}/locations/global/workloadIdentityPools/${pool}/providers/${provider}'"
}

stage_secrets() {
  say "secrets"
  # Random where a random value is a working value; a placeholder where a
  # real credential must be pasted in, so nothing looks configured that isn't.
  for name in django-secret-key db-password crawler-ro-password; do
    if have secrets describe "$name" --project="$PROJECT_ID"; then
      echo "  ${name} exists"
    else
      python3 -c "import secrets;print(secrets.token_urlsafe(48),end='')" \
        | gc secrets create "$name" --data-file=- --replication-policy=automatic
      echo "  ${name} created"
    fi
  done

  for name in google-oauth-client-id google-oauth-client-secret; do
    if have secrets describe "$name" --project="$PROJECT_ID"; then
      echo "  ${name} exists"
    else
      printf '' | gc secrets create "$name" --data-file=- --replication-policy=automatic
      echo "  ${name} created EMPTY — paste the OAuth client credential in:"
      echo "    gcloud secrets versions add ${name} --project=${PROJECT_ID} --data-file=-"
    fi
  done
  # settings.py strips whitespace and treats blank as unconfigured, so the
  # empty placeholders leave Google sign-in off rather than broken.
}

# Database on the crawler's shared instance. Creates only a database and a
# user; it never touches the crawler's or the directory's databases.
stage_sql() {
  say "database ${DB_NAME} on ${SQL_PROJECT}:${SQL_INSTANCE}"

  if gcloud sql databases describe "$DB_NAME" \
      --instance="$SQL_INSTANCE" --project="$SQL_PROJECT" >/dev/null 2>&1; then
    echo "  database exists"
  else
    gcloud sql databases create "$DB_NAME" \
      --instance="$SQL_INSTANCE" --project="$SQL_PROJECT"
  fi

  if gcloud sql users list --instance="$SQL_INSTANCE" --project="$SQL_PROJECT" \
      --format='value(name)' | grep -qx "$DB_USER"; then
    echo "  user exists"
  else
    local pw
    pw="$(gc secrets versions access latest --secret=db-password)"
    gcloud sql users create "$DB_USER" --instance="$SQL_INSTANCE" \
      --project="$SQL_PROJECT" --password="$pw"
    unset pw
  fi

  # The runtime reaches across projects to connect. This is the only
  # permission this project holds in the crawler's, and it grants nothing
  # but connection.
  gcloud projects add-iam-policy-binding "$SQL_PROJECT" \
    --member="serviceAccount:${RUN_SA}" \
    --role=roles/cloudsql.client --condition=None >/dev/null
  echo "  ${RUN_SA} may connect"
  echo
  echo "  connection name: ${SQL_PROJECT}:${REGION}:${SQL_INSTANCE}"
  echo
  echo "  NOT YET DONE — Cloud SQL added '${DB_USER}' to cloudsqlsuperuser, and"
  echo "  ownership and PUBLIC-CONNECT defaults are still open. Close them:"
  echo "    ./infra/sql/apply.sh isolate_datadesk_role.sql"
  echo "  Then create the crawler read-only role (SCOPE.md §1):"
  echo "    ./infra/sql/apply.sh create_crawler_readonly_role.sql"
}

# Cross-project read access to the analytics mirror and the maps-data cache.
stage_data() {
  say "read-only data access"

  # Dataset-level viewer on the analytics mirror: SELECT there, nothing else
  # in the crawler project. bq speaks dataset IAM; gcloud does not.
  if command -v bq >/dev/null; then
    if bq add-iam-policy-binding --project_id="$SQL_PROJECT" \
        --member="serviceAccount:${RUN_SA}" --role=roles/bigquery.dataViewer \
        "${SQL_PROJECT}:${BQ_DATASET}" >/dev/null 2>&1; then
      echo "  ${RUN_SA} may read ${SQL_PROJECT}:${BQ_DATASET}"
    else
      echo "  FAILED to bind on ${SQL_PROJECT}:${BQ_DATASET} — grant manually:"
      echo "    bq add-iam-policy-binding --project_id=${SQL_PROJECT} \\"
      echo "      --member=serviceAccount:${RUN_SA} \\"
      echo "      --role=roles/bigquery.dataViewer ${SQL_PROJECT}:${BQ_DATASET}"
    fi
  else
    echo "  bq not on PATH — grant the dataset binding manually (see above form)"
  fi

  # The visuals cache bucket (SCOPE.md §1): read/write, optional.
  if have storage buckets describe "gs://${MAPS_BUCKET}"; then
    gcloud storage buckets add-iam-policy-binding "gs://${MAPS_BUCKET}" \
      --member="serviceAccount:${RUN_SA}" --role=roles/storage.objectAdmin \
      --condition=None >/dev/null
    echo "  ${RUN_SA} may read/write gs://${MAPS_BUCKET}"
  else
    echo "  gs://${MAPS_BUCKET} not visible to this account — skipping (optional)"
  fi
}

# Run after the first deploy: map the hostname to the service. Free, unlike a
# load balancer; auth is in-app (allauth Google, domain-restricted), so no
# IAP and none of the directory's LB fallback stages.
stage_domain() {
  say "domain mapping ${ADMIN_HOST}"
  if ! have run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID"; then
    echo "  Cloud Run service '${SERVICE}' does not exist yet — deploy first, then rerun."
    return 1
  fi
  if have beta run domain-mappings describe --domain="$ADMIN_HOST" \
      --region="$REGION" --project="$PROJECT_ID"; then
    echo "  exists"
  else
    gc beta run domain-mappings create --service="$SERVICE" \
      --domain="$ADMIN_HOST" --region="$REGION"
  fi
  echo
  echo "  Route 53 record to create in localnewsimpact.org (rrdata from above):"
  echo "    ${ADMIN_HOST}.   CNAME   ghs.googlehosted.com."
  echo "  (a record for this exact name beats the *.localnewsimpact.org wildcard)"
}

STAGES=(project apis iam registry wif secrets sql data)

main() {
  local requested=("$@")
  [[ ${#requested[@]} -eq 0 ]] && requested=("${STAGES[@]}")
  [[ "${requested[0]:-}" == "all" ]] && requested=("${STAGES[@]}")

  for s in "${requested[@]}"; do
    "stage_${s}"
  done

  say "done"
  echo "  project  ${PROJECT_ID}  region ${REGION}"
  echo "  database ${SQL_PROJECT}:${REGION}:${SQL_INSTANCE}/${DB_NAME}"
  echo "  console  https://${ADMIN_HOST} (after deploy + domain stage)"
}

main "$@"
