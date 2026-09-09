"""MIG-WO3's RLS layer as a callable — the single source of truth.

The alembic migration (20260908_e8f1a3c5d7b9) runs this on upgrade;
tests/conftest.py's stamp_alembic_head() ALSO runs it, because test
modules that rebuild the schema with Base.metadata.create_all and stamp
head produce a HEAD-shaped schema whose tables were never touched by
any migration — without this, RLS/grants silently vanish mid-suite on
the Postgres leg (permission denied for role authenticated).

Deliberately dependency-free like core/dburl (importable by alembic
without app config): plain SQL over a DBAPI-capable connection.

Vanilla-Postgres portability: provisions the Supabase-shaped context
(`authenticated` role + auth.uid() shim) when missing; on hosted
Supabase both exist and everything is an idempotent no-op.
"""

USER_TABLES = [
    "profiles",
    "match_results",
    "application_drafts",
    "applications",
    "feedback",
    "ai_usage",
]
ALL_RLS_TABLES = USER_TABLES + ["users"]


def ensure_rls(connection) -> None:
    """Apply the full MIG-WO3 layer: context, grants, RLS, policies.
    Idempotent; no-op on non-PostgreSQL connections."""
    if connection.dialect.name != "postgresql":
        return

    def run(sql: str) -> None:
        connection.exec_driver_sql(sql)

    # --- Supabase-shaped context, provisioned when absent (vanilla PG) ---
    run(
        "DO $$ BEGIN"
        "  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated')"
        "  THEN CREATE ROLE authenticated NOLOGIN;"
        "  END IF;"
        "END $$"
    )
    run("CREATE SCHEMA IF NOT EXISTS auth")
    # The shim Supabase itself ships; on Supabase this replaces the
    # function with the identical body.
    run(
        "CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid"
        " LANGUAGE sql STABLE AS"
        " $$ SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid $$"
    )

    # --- Grants: authenticated may read the shared pool, write its own ---
    run("GRANT USAGE ON SCHEMA public TO authenticated")
    run("GRANT SELECT ON ALL TABLES IN SCHEMA public TO authenticated")
    run("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated")
    run(
        "GRANT INSERT, UPDATE, DELETE ON "
        + ", ".join(ALL_RLS_TABLES)
        + " TO authenticated"
    )
    # The ONE shared-pool write a route makes: manual job create
    # (POST /jobs — rate-limited, validated). INSERT only; every other
    # job_postings mutation is worker/pipeline work on the service
    # session, and users must never UPDATE or DELETE shared rows.
    run("GRANT INSERT ON job_postings TO authenticated")

    # --- RLS + policies ---
    for table in ALL_RLS_TABLES:
        key = "id" if table == "users" else "user_id"
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        run(f"DROP POLICY IF EXISTS own_rows ON {table}")
        run(
            f"CREATE POLICY own_rows ON {table}"
            f" FOR ALL"
            f" USING (auth.uid() IS NOT NULL AND {key} = auth.uid())"
            f" WITH CHECK (auth.uid() IS NOT NULL AND {key} = auth.uid())"
        )


def remove_rls(connection) -> None:
    """Drop the policies and disable RLS (the documented rollback:
    additive state, one switch per table). Grants/shim/role stay —
    inert without RLS, and dropping a role holding grants would
    cascade-revoke more than this layer owns. No-op off Postgres."""
    if connection.dialect.name != "postgresql":
        return
    for table in ALL_RLS_TABLES:
        connection.exec_driver_sql(f"DROP POLICY IF EXISTS own_rows ON {table}")
        connection.exec_driver_sql(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
