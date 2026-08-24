#!/usr/bin/env bash
#
# Apply a SQL script to the shared instance through the Cloud SQL Auth Proxy.
# The proxy authenticates with your gcloud credentials, so nothing is added to
# the instance's authorized networks and no password crosses the network in
# the clear.
#
#   ./infra/sql/apply.sh isolate_datadesk_role.sql
#   ./infra/sql/apply.sh create_crawler_readonly_role.sql
#
set -euo pipefail

SCRIPT="${1:?usage: apply.sh <file.sql>}"

# The usage above says a bare name, and psql resolves -f against the caller's
# directory, so `./infra/sql/apply.sh create_crawler_write_role.sql` -- the
# form this script documents -- failed with "No such file or directory" from
# anywhere but infra/sql. A bare name is resolved against this script's own
# directory; a path that exists is left alone.
if [ ! -f "$SCRIPT" ] && [ -f "$(dirname "$0")/$SCRIPT" ]; then
  SCRIPT="$(dirname "$0")/$SCRIPT"
fi
[ -f "$SCRIPT" ] || { echo "no such SQL file: $1" >&2; exit 1; }
INSTANCE="${INSTANCE:-mizzou-news-crawler:us-central1:mizzou-db-prod}"
SQL_PROJECT="${SQL_PROJECT:-mizzou-news-crawler}"
APP_PROJECT="${APP_PROJECT:-lnic-datadesk}"
PORT="${PORT:-5440}"
DB="${DB:-mizzou}"
USER="${USER_ROLE:-mizzou_user}"

command -v cloud-sql-proxy >/dev/null || {
  echo "cloud-sql-proxy not on PATH — https://github.com/GoogleCloudPlatform/cloud-sql-proxy/releases" >&2
  exit 1
}

cloud-sql-proxy --port "$PORT" "$INSTANCE" >/tmp/csp-datadesk.log 2>&1 &
PROXY=$!
trap 'kill "$PROXY" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  nc -z 127.0.0.1 "$PORT" 2>/dev/null && break
  sleep 1
done

# The role-creation scripts set the role's password from :pw; the value
# lives in the matching secret in this project.
EXTRA=()
case "$(basename "$SCRIPT")" in
  create_crawler_readonly_role.sql) PW_SECRET=crawler-ro-password ;;
  create_crawler_write_role.sql)    PW_SECRET=crawler-rw-password ;;
  *) PW_SECRET= ;;
esac
if [ -n "$PW_SECRET" ]; then
  EXTRA=(-v "pw=$(gcloud secrets versions access latest \
    --secret="$PW_SECRET" --project="$APP_PROJECT")")
fi

# The role must be a member of cloudsqlsuperuser to alter database privileges.
PGPASSWORD="$(gcloud secrets versions access latest --secret=db-password --project="$SQL_PROJECT")" \
  psql -h 127.0.0.1 -p "$PORT" -U "$USER" -d "$DB" -v ON_ERROR_STOP=1 \
    "${EXTRA[@]}" -f "$SCRIPT"

# The datadesk half must run as the datadesk role against its own database,
# because ownership has moved and the crawler's role can no longer alter it.
if [ "$(basename "$SCRIPT")" = "isolate_datadesk_role.sql" ]; then
  echo
  echo "== second half, as the datadesk role =="
  PGPASSWORD="$(gcloud secrets versions access latest --secret=db-password --project="$APP_PROJECT")" \
    psql -h 127.0.0.1 -p "$PORT" -U datadesk -d datadesk -v ON_ERROR_STOP=1 \
      -f "$(dirname "$SCRIPT")/harden_datadesk_db.sql"
fi
