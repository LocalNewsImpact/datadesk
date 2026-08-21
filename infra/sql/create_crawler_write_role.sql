-- The audited write role for review/cleanup (SCOPE.md §2.2, boundary §6.5).
--
-- Column-level grants: Postgres enforces the boundary, so an app bug
-- cannot touch a column the pipeline owns. The app gates who may use the
-- role (editor/admin) and records every write in the append-only audit
-- log with actor, before/after, and reason.
--
-- No INSERT, no DELETE, anywhere: Datadesk corrects records, it does not
-- create or destroy them. Dataset/source creation is Phase 4 and gets its
-- grants when the forms exist.
--
-- SELECT comes with it: an UPDATE without SELECT cannot read the row it
-- is changing (RETURNING, WHERE on current values), and the ORM reads
-- before it writes.
--
-- Run as mizzou_user against mizzou, like the read role. Created through
-- psql, not the Cloud SQL API, so the role never joins cloudsqlsuperuser.
-- The password is passed as :pw (apply.sh reads crawler-rw-password).
--
--   ./infra/sql/apply.sh create_crawler_write_role.sql

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'datadesk_rw') THEN
    CREATE ROLE datadesk_rw LOGIN;
  END IF;
END
$$;
ALTER ROLE datadesk_rw PASSWORD :'pw';

GRANT CONNECT ON DATABASE mizzou TO datadesk_rw;
GRANT USAGE ON SCHEMA public TO datadesk_rw;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datadesk_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE mizzou_user IN SCHEMA public
  GRANT SELECT ON TABLES TO datadesk_rw;

-- The boundary. Everything not listed here stays impossible.
GRANT UPDATE (author, title, content, text, status, wire_check_status)
  ON articles TO datadesk_rw;
GRANT UPDATE (skip_reason, geo_skip_reason)
  ON article_enrichment TO datadesk_rw;
GRANT UPDATE (canonical_name, city, county, owner, type)
  ON sources TO datadesk_rw;

\echo ''
\echo 'column grants held by datadesk_rw (expect only the boundary):'
SELECT table_name, column_name, privilege_type
FROM information_schema.column_privileges
WHERE grantee = 'datadesk_rw'
ORDER BY table_name, column_name;
