"""
Database configuration for JobFinderOS.

Follows the TalentHive pattern (engine + SessionLocal + get_db + init_db)
but defaults to SQLite so the app runs with zero external services.
Set DATABASE_URL to PostgreSQL for production.
"""

import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# Single source of truth: settings (pydantic-settings) — this is the value
# from real env vars AND backend/.env (model_config env_file). Never read
# os.getenv here: it bypasses .env loading and silently ignores the file.
from app.core.config import settings as _settings
from app.core.dburl import normalize_postgres_url
from app.core.orm import Base as Base  # re-export: models/tests import Base from here

DATABASE_URL = _settings.DATABASE_URL


# Render/Heroku-style and bare-postgresql URLs normalize to the ONE
# installed driver (psycopg 3) — see normalize_postgres_url
DATABASE_URL = normalize_postgres_url(DATABASE_URL)

logger.info("Database URL configured: %s", DATABASE_URL.split("@")[-1])

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # SQLite connections cannot cross threads by default; FastAPI uses threads.
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# MIG-WO2: the async engine/ASYNC_DATABASE_URL machinery is deleted —
# it existed only for fastapi-users' async adapter. Auth now verifies
# Supabase JWTs on the sync engine (app/users.py).


# ---------------------------------------------------------------------------
# MIG-WO3: the two-session story, made structural.
#
#   SessionLocal        — SERVICE sessions: the hunt/scheduler/pipeline
#                         machinery, worker cron, scripts, and the tests'
#                         seeding. Plain postgres (table owner): RLS is not
#                         enforced for the owner, so shared-pool work
#                         (job_postings, scrape_runs, locks) keeps running.
#
#   RequestSessionLocal — INTERACTIVE sessions: the only factory get_db
#                         hands to routes. On Postgres every transaction
#                         SETs LOCAL role 'authenticated' plus
#                         request.jwt.claim.sub from the request context
#                         (the same contextvar set_user_context_middleware
#                         already stamps for ai_usage attribution). The
#                         role is set even WITHOUT a known sub: an
#                         unidentified session runs as authenticated with
#                         auth.uid() NULL, and the policies' IS NOT NULL
#                         guard filters EVERYTHING — fail-closed, the
#                         documented Supabase stance for NULL identities.
#                         RLS itself is additive Postgres state owned by
#                         the MIG-WO3 migration; on SQLite (tests, local
#                         dev) the listener is a no-op.
# ---------------------------------------------------------------------------

RequestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _request_jwt_sub() -> "str | None":
    """The caller's id from the request context, or None. The middleware
    parses it as a UUID before storing, and we re-stringify through
    uuid.UUID so a SET statement can never carry injected syntax."""
    import uuid as _uuid

    from app.services.ai_service import current_user_id

    value = current_user_id.get(None)
    if value is None:
        return None
    return str(_uuid.UUID(str(value)))


def _propagate_request_jwt(session, transaction, connection) -> None:
    """SQLAlchemy 'after_begin' listener on RequestSessionLocal: runs on
    the FIRST use of a connection inside a transaction, before any
    statement of ours — the right moment for transaction-scoped SET
    LOCAL (per Supabase's RLS direct-connection pattern)."""
    if connection.dialect.name != "postgresql":
        return  # SQLite: no roles, no RLS — the migration never enables it
    sub = _request_jwt_sub()  # uuid.UUID-round-tripped: injection-proof
    connection.exec_driver_sql("SET LOCAL role 'authenticated'")
    if sub is not None:
        connection.exec_driver_sql(f"SET LOCAL request.jwt.claim.sub = '{sub}'")


from sqlalchemy import event as _sa_event  # noqa: E402

_sa_event.listen(RequestSessionLocal, "after_begin", _propagate_request_jwt)



def get_db():
    """FastAPI dependency yielding a REQUEST session (MIG-WO3: RLS-
    propagated on Postgres — see RequestSessionLocal above)."""
    db = RequestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize/migrate the schema — Alembic owns BOTH backends now.

    - Postgres: upgrade head directly.
    - SQLite: fresh DB -> upgrade head from scratch; legacy create_all DB ->
      stamp at the initial revision (its historical shape) then upgrade, so
      local databases migrate into the per-user schema automatically.
    """
    from alembic.config import Config
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text

    from alembic import command

    ini = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
    cfg = Config(str(ini))
    # DATABASE_URL is already the sync psycopg URL — asyncpg is gone
    cfg.set_main_option("sqlalchemy.url", DATABASE_URL)

    if DATABASE_URL.startswith("postgres"):
        # Serialize boots across processes (WO-07): on a Render blueprint
        # deploy the web service and the hunt cron start near-simultaneously,
        # and alembic's `upgrade head` takes no lock of its own — two
        # concurrent runs both apply a pending migration and one crashes on
        # duplicate DDL. Postgres advisory locks are the official primitive
        # for this: the loser blocks until the winner finishes, then sees
        # alembic_version already at head and no-ops. Session-scoped on a
        # dedicated connection (command.upgrade opens its own); process
        # death between lock and unlock releases it with the connection.
        _MIGRATION_LOCK_KEY = 821371
        with engine.connect() as lock_conn:
            # Bounded wait (review r4): without a timeout, a wedged holder
            # (stuck migration, orphaned session) blocks every boot forever
            # with no error. Verified on PG17: lock_timeout aborts an
            # advisory-lock wait with 'canceling statement due to lock
            # timeout' — boot then FAILS LOUDLY instead of hanging.
            # SET LOCAL (not SET): scoped to this transaction so the
            # pooled connection doesn't carry lock_timeout into later
            # application queries; the pg_advisory_lock below runs in
            # the same transaction, so the bounded wait still applies.
            lock_conn.execute(sa_text("SET LOCAL lock_timeout = '120s'"))
            lock_conn.execute(sa_text(
                f"SELECT pg_advisory_lock({_MIGRATION_LOCK_KEY})"))
            try:
                command.upgrade(cfg, "head")
            finally:
                lock_conn.execute(sa_text(
                    f"SELECT pg_advisory_unlock({_MIGRATION_LOCK_KEY})"))
        logger.info("Alembic migrations applied (postgres, advisory-locked)")
        return

    insp = sa_inspect(engine)
    has_tables = bool(insp.get_table_names())
    has_version = "alembic_version" in insp.get_table_names()

    if not has_tables:
        command.upgrade(cfg, "head")
        logger.info("Fresh SQLite database created via Alembic")
        return
    if not has_version:
        # Legacy create_all database: its shape matches the initial migration
        with engine.connect() as conn:
            conn.execute(
                sa_text(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num VARCHAR(32) NOT NULL)"
                )
            )
            conn.execute(
                sa_text("INSERT INTO alembic_version (version_num) VALUES ('ab219adaba28')")
            )
            conn.commit()
        logger.info("Stamped legacy SQLite schema at ab219adaba28")
    command.upgrade(cfg, "head")
    logger.info("SQLite migrated to head via Alembic")

