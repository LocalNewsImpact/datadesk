-- The read-only role Datadesk uses to read the crawler's database (SCOPE.md
-- §1: articles, enrichment, datasets, sources, gazetteer).
--
-- Writes stay impossible by Postgres's enforcement rather than by our care:
-- until the Phase 2 write-role boundary is decided (SCOPE.md §6.5), this is
-- the ONLY path Datadesk has into `mizzou`, and it can only SELECT.
--
-- Run as mizzou_user (a cloudsqlsuperuser) against the mizzou database — it
-- owns the tables, so both the role creation and the grants land in one
-- connection, and ALTER DEFAULT PRIVILEGES covers tables the crawler's
-- migrations add later (without it a new table would be invisible and a
-- dashboard would quietly lose a column rather than fail).
--
-- Created through psql, not the Cloud SQL API, so the role is never added to
-- cloudsqlsuperuser and there is nothing to strip afterwards.
--
-- The password is passed as :pw (apply.sh reads the crawler-ro-password
-- secret). It is set with a plain statement rather than inside a DO block,
-- because psql substitutes variables client-side and never looks inside a
-- dollar-quoted string.
--
--   ./infra/sql/apply.sh create_crawler_readonly_role.sql

\set ON_ERROR_STOP on

SELECT 'role exists, updating password' WHERE EXISTS
  (SELECT FROM pg_roles WHERE rolname = 'datadesk_ro');

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'datadesk_ro') THEN
    CREATE ROLE datadesk_ro LOGIN;
  END IF;
END
$$;
ALTER ROLE datadesk_ro PASSWORD :'pw';

GRANT CONNECT ON DATABASE mizzou TO datadesk_ro;
GRANT USAGE ON SCHEMA public TO datadesk_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datadesk_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO datadesk_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE mizzou_user IN SCHEMA public
  GRANT SELECT ON TABLES TO datadesk_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE mizzou_user IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO datadesk_ro;

\echo ''
\echo 'privileges held by datadesk_ro (expect SELECT and nothing else):'
SELECT DISTINCT privilege_type
FROM information_schema.table_privileges
WHERE grantee = 'datadesk_ro'
ORDER BY privilege_type;
