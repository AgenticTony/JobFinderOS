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
from app.core.dburl import async_database_url, normalize_postgres_url
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

# Async engine for the auth layer (fastapi-users) — same database, async driver
ASYNC_DATABASE_URL = async_database_url(DATABASE_URL)



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

