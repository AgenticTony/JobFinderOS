-- WO-25 layer 2: the least-privilege role the CI drift check logs in as.
--
-- OWNER-RUN, once, in the Supabase SQL editor (runs as postgres). Not a
-- migration: the password must never enter the repo.
--
-- Can do exactly one thing: SELECT public.alembic_version. It is NOT
-- anon/authenticated, gets no default privileges, and never inherits.
--
-- Why a policy: rls_sql.py enables RLS on alembic_version with NO
-- policies (LOCKED_SERVICE_TABLES, deny-all for the Data API roles).
-- Without the policy below this role reads ZERO rows — the drift script
-- fails closed on that, so a missing policy shows up as a red job, not a
-- silent pass. ensure_rls() only ENABLEs RLS on this table; it never
-- drops foreign policies, so this survives every boot.
--
-- Replace <STRONG-RANDOM-PASSWORD> (e.g. `openssl rand -base64 32`).

CREATE ROLE ci_revision_reader
    LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD '<STRONG-RANDOM-PASSWORD>'
    CONNECTION LIMIT 2;

-- Every statement bounded: a CI job must never hold anything open.
ALTER ROLE ci_revision_reader SET statement_timeout = '10s';
ALTER ROLE ci_revision_reader SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE ci_revision_reader SET default_transaction_read_only = on;

GRANT USAGE ON SCHEMA public TO ci_revision_reader;
GRANT SELECT ON public.alembic_version TO ci_revision_reader;

CREATE POLICY revision_reader ON public.alembic_version
    FOR SELECT TO ci_revision_reader USING (true);

-- GitHub secret PROD_REVISION_READER_URL (repo Settings → Secrets →
-- Actions), session pooler, Supabase's tenant-qualified user name:
--   postgresql://ci_revision_reader.<project-ref>:<password>@aws-1-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require
--
-- Verify (as the new role): SELECT version_num FROM alembic_version;
--   → exactly one row. Then: SELECT count(*) FROM profiles; → permission denied.
--
-- Rollback:
--   DROP POLICY revision_reader ON public.alembic_version;
--   REVOKE ALL ON public.alembic_version FROM ci_revision_reader;
--   REVOKE USAGE ON SCHEMA public FROM ci_revision_reader;
--   DROP ROLE ci_revision_reader;
