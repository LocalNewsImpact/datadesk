-- The audited write role for review/cleanup (SCOPE.md §2.2, boundary §6.5).
--
-- Column-level grants: Postgres enforces the boundary, so an app bug
-- cannot touch a column the pipeline owns. The app gates who may use the
-- role (editor/admin) and records every write in the append-only audit
-- log with actor, before/after, and reason.
--
-- Articles are corrected, never created or destroyed: no INSERT or
-- DELETE there. Phase 4 (dataset management) adds creation of sources
-- and datasets and membership changes — the only DELETE anywhere is
-- dataset_sources rows, because membership is a mapping, not a record.
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
--
-- `metadata` is here so a decision reaches the pipeline. The crawler
-- raises a hold from the article's own fields, so a claim a reviewer
-- has answered is raised again by the next run that reads those fields:
-- held, released from the console, held again, with the reviewer's
-- decision undone by a stage that never knew it was made. The console
-- writes the answer onto the article, under `review_decided`, because
-- the two databases do not join and that is the only place both can
-- see it.
--
-- The crawler writes this column too -- the hold note lives beside the
-- decisions -- so both sides read and merge rather than replace. The
-- console never writes a bare object over it.
GRANT UPDATE (author, title, content, text, status, wire_check_status,
              metadata)
  ON articles TO datadesk_rw;
-- scope and scope_confidence are here so a reviewer can correct a
-- mislabelled scope from the review queue. When a person sets the
-- scope, scope_confidence is written as NULL: confidence is the model's
-- estimate of its own answer, and a human answer does not have one.
-- Writing 1.0 instead would make human decisions look like the model's
-- most certain predictions in every confidence filter and chart.
GRANT UPDATE (skip_reason, geo_skip_reason, scope, scope_confidence)
  ON article_enrichment TO datadesk_rw;
-- `metadata` carries the state, which is a required field of a publisher
-- record. Postgres grants a column, not a key inside one, so this is wider
-- than the application allows -- review/services.py permits `meta.state`
-- and nothing else in that blob. The narrower list is the one a reviewer
-- can act through; this only has to stop being the thing that refuses.
--
-- The paywall panel is on this list too: whether a publication has one,
-- what a subscription costs and where a person signs in are edited on the
-- record. Not `auth_secret_name` and not `auth_config` -- those are the
-- crawler's login automation, and the secret they name is the one thing
-- here that must not be settable through the console.
-- `auth_secret_name` is on it so the paywall page can record where a
-- credential was stored. The name is derived from the host and never
-- typed; the secret itself is in Secret Manager, in the crawler's
-- project, and nothing in this database holds a password.
GRANT UPDATE (canonical_name, city, county, owner, type, metadata,
              has_paywall, subscription_cost, subscription_period, login_url,
              auth_secret_name)
  ON sources TO datadesk_rw;

-- Phase 4: dataset management (SCOPE.md §2.4). Creation columns include
-- the NOT-NULL-without-server-default pair on sources; everything else
-- falls to the table defaults.
GRANT INSERT (id, host, host_norm, canonical_name, city, county, owner,
              type, status, metadata,
              rss_consecutive_failures, rss_transient_failures)
  ON sources TO datadesk_rw;
GRANT INSERT (id, slug, label, name, description, metadata, cron_enabled,
              owner_name, owner_email)
  ON datasets TO datadesk_rw;
GRANT UPDATE (name, description, metadata, cron_enabled,
              owner_name, owner_email)
  ON datasets TO datadesk_rw;
GRANT INSERT (id, dataset_id, source_id, legacy_host_id, legacy_meta)
  ON dataset_sources TO datadesk_rw;
GRANT DELETE ON dataset_sources TO datadesk_rw;

\echo ''
\echo 'column grants held by datadesk_rw (expect only the boundary):'
SELECT table_name, column_name, privilege_type
FROM information_schema.column_privileges
WHERE grantee = 'datadesk_rw'
ORDER BY table_name, column_name;
