-- Second half of the isolation, run as the `datadesk` role against its own
-- database. Separate from isolate_datadesk_role.sql because once ownership
-- has moved, the crawler's role can no longer alter these privileges —
-- attempting it there produces a warning rather than an error, easy to miss.
--
-- Idempotent: safe to rerun.

\set ON_ERROR_STOP on

REVOKE CONNECT ON DATABASE datadesk FROM PUBLIC;

\echo ''
\echo 'datadesk database access list (PUBLIC should hold =T only, no c):'
SELECT datname, array_to_string(datacl, ' , ') AS acl
FROM pg_database WHERE datname = 'datadesk';

-- The schema inside it too, or migrations can create nothing.
GRANT ALL ON SCHEMA public TO datadesk;
