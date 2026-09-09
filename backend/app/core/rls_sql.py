"""MIG-WO3's RLS layer as a callable — the single source of truth.

The alembic migration (20260908_e8f1a3c5d7b9) runs this on upgrade;
init_db() re-runs it after every `upgrade head` (per-boot convergence);
tests/conftest.py's stamp_alembic_head() runs it, because test modules
that rebuild the schema with Base.metadata.create_all and stamp head
produce a HEAD-shaped schema whose tables were never touched by any
migration.

Deliberately dependency-free like core/dburl (importable by alembic
without app config): plain SQL over a DBAPI-capable connection.

Vanilla-Postgres portability: provisions the Supabase-shaped context
(`authenticated` role + auth.uid() shim) when missing — GUARDED, never
replaced: on hosted Supabase auth.uid() is owned by supabase_auth_admin
and a CREATE OR REPLACE from postgres aborts the migration (audit round
3, 2026-09-09 — cutover-fatal); the real function also coalesces the
request.jwt.claims JSON GUC that PostgREST writes, so replacing it
would break the Data API even where ownership allowed it.

Coverage (audit round 3): EVERY table in public carries RLS — either an
own_rows policy (identity tables) or bare ENABLE with NO policy (the
service tables: shared-pool machinery the anon/authenticated roles must
never reach through the Data API; the table owner — the worker/pipeline
connection — bypasses RLS, so operations are unaffected). Supabase's
defaults grant anon/authenticated full DML on public tables, and the
anon key is published to every browser by design: without RLS on those
tables the job pool, scrape bookkeeping and the hunt lock are reachable
with the publishable key alone.
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

# Service tables: shared-pool machinery. RLS enabled with NO policies —
# deny-all for anon/authenticated (the Data API path), owner-bypassed
# for the worker/pipeline sessions. job_postings and scrape_runs are
# READ by request sessions (and job_postings INSERTed by manual create),
# so they carry explicit read/insert policies for authenticated.
SHARED_READ_TABLES = ["job_postings", "scrape_runs"]
LOCKED_SERVICE_TABLES = ["scrape_watermarks", "system_locks", "alembic_version"]


def rls_layer_complete(connection) -> bool:
    """True when every public table carries relrowsecurity and the
    identity tables carry own_rows. The cheap pre-check that lets
    ensure_rls skip its ALTER TABLE section on the common boot — ENABLE
    ROW LEVEL SECURITY takes ACCESS EXCLUSIVE per table, and per-boot
    exclusive locks wedge against any idle-in-transaction pooled
    session. Covers EVERY public table (audit round 3): a table without
    RLS is the fail-open state regardless of which list it belongs to."""
    if connection.dialect.name != "postgresql":
        return True
    rows = connection.exec_driver_sql(
        "SELECT c.relname, c.relrowsecurity, "
        "(SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' "
        " AND p.tablename = c.relname AND p.policyname = 'own_rows') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
    ).fetchall()
    if not rows:
        return False  # nothing to protect yet — never report complete
    return all(relayed for _name, relayed, _p in rows) and all(
        p == 1 for name, _r, p in rows if name in ALL_RLS_TABLES
    )


def ensure_rls(connection) -> None:
    """Apply the full MIG-WO3 layer: context, grants, RLS, policies.
    Idempotent; no-op on non-PostgreSQL connections. The ALTER/policy
    section is SKIPPED when the layer is already complete — those
    statements take ACCESS EXCLUSIVE locks, and running them on every
    boot (init_db calls this) would wedge against pooled sessions."""
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
    # The shim, GUARDED: only when auth.uid() does not exist. On hosted
    # Supabase it exists and is owned by supabase_auth_admin — CREATE OR
    # REPLACE from postgres aborts (cutover-fatal), and the real body
    # coalesces request.jwt.claims, which ours must never clobber.
    run(
        "DO $$ BEGIN"
        "  IF to_regprocedure('auth.uid()') IS NULL THEN"
        "    CREATE FUNCTION auth.uid() RETURNS uuid"
        " LANGUAGE sql STABLE AS"
        " $f$ SELECT NULLIF(current_setting('request.jwt.claim.sub', true),"
        " '')::uuid $f$;"
        "  END IF;"
        "END $$"
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

    # Point-in-time grants are not enough (MIG-WO3 review, 2026-09-09):
    # ON ALL TABLES covers what exists NOW — the next migration that
    # adds a table would leave it ungranted, and request sessions would
    # hit "permission denied" on first touch (the exact symptom the
    # conftest stamp path already recorded once). Default privileges
    # cover everything postgres creates in public from here on.
    run(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT ON TABLES TO authenticated"
    )
    run(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO authenticated"
    )

    # --- RLS + policies --- (ACCESS EXCLUSIVE per table — the ONLY
    # section gated on completeness: grants above are lock-light and
    # always re-asserted)
    if rls_layer_complete(connection):
        return

    # Identity tables: own_rows, with the two documented conventions
    # (audit round 3): TO authenticated stops policy evaluation for
    # other roles instead of running the predicate; (select auth.uid())
    # wraps the call in an initPlan the optimizer caches per statement —
    # the point on match_results/ai_usage row counts.
    for table in ALL_RLS_TABLES:
        key = "id" if table == "users" else "user_id"
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        run(f"DROP POLICY IF EXISTS own_rows ON {table}")
        run(
            f"CREATE POLICY own_rows ON {table}"
            f" FOR ALL TO authenticated"
            f" USING ((select auth.uid()) IS NOT NULL AND {key} = (select auth.uid()))"
            f" WITH CHECK ((select auth.uid()) IS NOT NULL AND {key} = (select auth.uid()))"
        )

    # Shared-read tables: readable by authenticated (the request surface
    # reads the pool and scrape bookkeeping), insertable only where a
    # route does it (manual job create). No UPDATE/DELETE, ever.
    for table in SHARED_READ_TABLES:
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        run(f"DROP POLICY IF EXISTS shared_read ON {table}")
        run(
            f"CREATE POLICY shared_read ON {table}"
            f" FOR SELECT TO authenticated USING (true)"
        )
    run("DROP POLICY IF EXISTS manual_create ON job_postings")
    run(
        "CREATE POLICY manual_create ON job_postings"
        " FOR INSERT TO authenticated WITH CHECK (true)"
    )

    # Locked service tables: RLS with NO policy — deny-all for
    # anon/authenticated through the Data API; the owner (worker,
    # pipeline, alembic) bypasses RLS and is unaffected.
    for table in LOCKED_SERVICE_TABLES:
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


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
    for table in SHARED_READ_TABLES:
        connection.exec_driver_sql(f"DROP POLICY IF EXISTS shared_read ON {table}")
        connection.exec_driver_sql(f"DROP POLICY IF EXISTS manual_create ON {table}")
        connection.exec_driver_sql(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in LOCKED_SERVICE_TABLES:
        connection.exec_driver_sql(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
