"""Alembic env policy — URL precedence (DATA-4) and DDL lock timeouts
(DATA-6), extracted here so tests can pin them.

Dependency-free by the SAME contract as app.core.dburl: alembic's env.py
imports this in the migration-container shape (Render pre-deploy, k8s
init container, one-off docker run) where DATABASE_URL is set and little
else is — importing app.core.config/database here would trip the
AUTH_SECRET production guard (WO-11 round 2). Only the database URL is
ever logged, and only the part after the last "@" (credential scrubbing
matches app.core.database).
"""

import logging
import os

from sqlalchemy import event

logger = logging.getLogger("alembic.env")

# Final fallback when the caller injected no URL AND the environment
# carries no DATABASE_URL: a local sqlite file (the local-dev default).
SQLITE_FALLBACK_URL = "sqlite:///./jobfinderos.db"

# DATA-6: how long a migration's DDL may WAIT for a lock before failing
# loudly, in seconds. 30s because the boot-side advisory-lock wait is
# bounded at 120s (app.core.database.init_db) — the DDL wait must be the
# SHORTER of the two so the overall boot fails inside ~2.5 minutes
# instead of hanging past the Render health check with no error.
MIGRATION_LOCK_TIMEOUT_SECONDS = 30


def _masked(url: str) -> str:
    """URL for logs: everything up to the last '@' (credentials) dropped."""
    return url.split("@")[-1]


def resolve_url(config, environ=None) -> str:
    """Pick the database URL for this migration run (DATA-4).

    Precedence:
      1. A URL the CALLER injected into the alembic Config — init_db()
         sets sqlalchemy.url from app settings, which is real env vars
         MERGED with backend/.env (pydantic-settings env_file).
      2. The DATABASE_URL environment variable — only when the config
         carries no URL. This is the `alembic upgrade head` CLI shape
         (CI's minimal-env step, the migration container): alembic.ini
         deliberately defines no sqlalchemy.url, so the env var is the
         only source.
      3. The local sqlite fallback.

    env.py used to let (2) override (1) UNCONDITIONALLY: on a machine
    whose DATABASE_URL lived only in backend/.env, init_db resolved the
    correct URL from settings, then env.py stomped it with the env
    default and the boot silently migrated a fresh ./jobfinderos.db
    (Render was unaffected — it exports DATABASE_URL for real).

    Returns the winner normalized through the ONE normalizer
    (app.core.dburl) — idempotent for already-normalized URLs.
    """
    if environ is None:
        environ = os.environ

    injected = None
    try:
        injected = config.get_main_option("sqlalchemy.url")
    except KeyError:  # pragma: no cover - alembic returns None today
        injected = None

    if injected:
        logger.info("alembic URL from injected config value: %s", _masked(injected))
        url = injected
    elif environ.get("DATABASE_URL"):
        logger.info("alembic URL from DATABASE_URL env var: %s",
                    _masked(environ["DATABASE_URL"]))
        url = environ["DATABASE_URL"]
    else:
        logger.info("alembic URL: no config value, no DATABASE_URL — "
                    "local sqlite fallback")
        url = SQLITE_FALLBACK_URL

    from app.core.dburl import normalize_postgres_url

    url = normalize_postgres_url(url)
    # Sync drivers only in alembic (it runs sync engines).
    return url.replace("sqlite+aiosqlite://", "sqlite://", 1)


def apply_lock_timeout(dbapi_connection, connection_record) -> None:
    """CONNECT-event body: bound lock waiting for this migration run.

    Session-scoped, not LOCAL: alembic's connection is dedicated to the
    whole upgrade (NullPool, one connection) and may commit per
    migration (transaction_per_migration), which would revert a SET
    LOCAL after the first commit — the timeout would silently vanish
    for every migration after the first.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(
            "SET SESSION lock_timeout = "
            f"'{MIGRATION_LOCK_TIMEOUT_SECONDS}s'"
        )
    finally:
        cursor.close()


def register_migration_timeouts(engine):
    """DATA-6: bound how long migration DDL may WAIT on locks.

    The advisory-lock connection in init_db already carries a 120s
    lock_timeout, but that bounds only the pg_advisory_lock WAIT — the
    migration connection alembic opens itself had NO timeout, so the
    first DDL statement that lands behind a long-running transaction
    (a big application write, an autovacuum, another service's
    migration) blocked unboundedly: no error, no log line, just a boot
    that never finishes until the platform kills it.

    lock_timeout ONLY — deliberately no statement_timeout: a statement
    timeout would also abort a LONG BUT LEGITIMATE migration (a backfill
    or index build on a growing table) halfway through a deploy. Waiting
    for a lock is always waste; running long is sometimes correct.

    No-op on non-postgresql dialects (sqlite has no lock_timeout and
    would raise on the SET).
    """
    if engine.dialect.name != "postgresql":
        return engine
    event.listens_for(engine, "connect")(apply_lock_timeout)
    logger.info("migration DDL lock_timeout = %ss (fail loudly, never hang)",
                MIGRATION_LOCK_TIMEOUT_SECONDS)
    return engine
