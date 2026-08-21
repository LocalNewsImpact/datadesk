-- Isolate the datadesk role from the other databases on the shared instance.
--
-- Ported from NewsSourceDirectory/infra/sql/isolate_directory_role.sql, which
-- found and closed two defaults rather than mistakes:
--
--   1. Postgres grants CONNECT on every database to PUBLIC, so the `datadesk`
--      role could open the crawler's `mizzou` database and the directory's.
--   2. Cloud SQL adds every API-created user to `cloudsqlsuperuser`. That is
--      the larger hole: membership confers rights over the whole instance, so
--      revoking CONNECT alone would not close anything.
--
-- Order matters. Ownership is granted before privileges are removed, so the
-- datadesk role never loses the ability to run its own migrations.
--
-- Idempotent: safe to rerun.
--
--   ./infra/sql/apply.sh isolate_datadesk_role.sql

\set ON_ERROR_STOP on
BEGIN;

-- 1. The datadesk role must own its own database before it stops being a
--    superuser, or migrations lose the right to create tables.
--
--    Transferring ownership requires the current owner to be a member of the
--    incoming one, which shared membership of cloudsqlsuperuser does not
--    confer. So the membership is granted, used, and given straight back.
GRANT datadesk TO mizzou_user;
ALTER DATABASE datadesk OWNER TO datadesk;
REVOKE datadesk FROM mizzou_user;

-- 2. Re-assert the crawler database's closed door. The directory work already
--    revoked PUBLIC; rerunning is harmless and keeps this script a complete
--    description on its own.
GRANT CONNECT ON DATABASE mizzou TO mizzou_user;
GRANT CONNECT ON DATABASE mizzou TO datastream_user;
REVOKE CONNECT ON DATABASE mizzou FROM PUBLIC;

-- The mirror-image lock on the datadesk database cannot run here: once
-- ownership moves, this connection is no longer entitled to change it. It
-- lives in harden_datadesk_db.sql, which apply.sh runs next as datadesk.

-- 3. Remove the escalation path. Without this the revokes above are theatre:
--    a member of cloudsqlsuperuser can SET ROLE and undo them.
REVOKE cloudsqlsuperuser FROM datadesk;

COMMIT;

-- Verification. datacl should list explicit grants rather than being null,
-- and datadesk should hold no role memberships.
\echo ''
\echo 'database access lists:'
SELECT datname, coalesce(array_to_string(datacl, ' , '), '(default: PUBLIC may connect)') AS acl
FROM pg_database WHERE datname IN ('mizzou', 'directory', 'datadesk') ORDER BY datname;

\echo ''
\echo 'role memberships for datadesk (expected: none):'
SELECT g.rolname AS member_of
FROM pg_auth_members m
JOIN pg_roles r ON r.oid = m.member
JOIN pg_roles g ON g.oid = m.roleid
WHERE r.rolname = 'datadesk';
