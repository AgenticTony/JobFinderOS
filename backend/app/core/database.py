"""
Database configuration for JobFinderOS.

Follows the TalentHive pattern (engine + SessionLocal + get_db + init_db)
but defaults to SQLite so the app runs with zero external services.
Set DATABASE_URL to PostgreSQL for production.
"""

import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

# Single source of truth: settings (pydantic-settings) — this is the value
# from real env vars AND backend/.env (model_config env_file). Never read
# os.getenv here: it bypasses .env loading and silently ignores the file.
from app.core.config import settings as _settings

DATABASE_URL = _settings.DATABASE_URL


def normalize_postgres_url(url: str) -> str:
    """Postgres URLs arrive in three shapes: postgres:// (Render/Heroku
    convention), bare postgresql:// (by habit), postgresql+psycopg://
    (explicit). SQLAlchemy resolves bare postgresql:// to the psycopg2
    dialect — and psycopg2 is NOT installed (psycopg 3 is the one driver,
    WO-11). Everything normalizes to +psycopg so the SYNC engine can
    actually connect; this is the same protection async_database_url
    gives the auth engine.
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def async_database_url(url: str) -> str:
    """Resolve the DATABASE_URL for the async auth engine.

    fastapi-users' SQLAlchemy adapter is async-only (official docs), so the
    auth layer runs on a second engine over the same database. ONE driver
    covers both engines (WO-11 / ARCHITECTURE F2): SQLAlchemy 2.0's
    postgresql+psycopg:// dialect serves create_engine AND
    create_async_engine. asyncpg is deliberately absent — it is documented
    to fail on BOTH Supabase poolers (prepared statements), and nothing
    deploys while the auth layer runs a driver that cannot reach the
    production database. sqlite -> aiosqlite stays for local/tests.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url

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

# Async engine for the auth layer (fastapi-users) — same database, async driver
ASYNC_DATABASE_URL = async_database_url(DATABASE_URL)

Base = declarative_base()


def get_db():
    """FastAPI dependency yielding a database session."""
    db = SessionLocal()
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
        command.upgrade(cfg, "head")
        logger.info("Alembic migrations applied (postgres)")
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

