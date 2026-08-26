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


def async_database_url(url: str) -> str:
    """Translate the sync DATABASE_URL to its async driver counterpart.

    fastapi-users' SQLAlchemy adapter is async-only (official docs), so the
    auth layer runs on a second engine over the same database:
    postgresql+psycopg:// -> postgresql+asyncpg://, sqlite -> sqlite+aiosqlite.
    """
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url

# Render/Heroku-style URLs use postgres:// — SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

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
    """Initialize the schema.

    Postgres environments (Neon/CI/production): Alembic owns the schema —
    `upgrade head` runs on boot; create_all is never used there. SQLite dev
    keeps the original create_all + light column migrations so existing
    local databases keep working unchanged.
    """
    if DATABASE_URL.startswith("postgres"):
        from alembic.config import Config

        from alembic import command

        ini = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
        cfg = Config(str(ini))
        cfg.set_main_option(
            "sqlalchemy.url",
            DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1),
        )
        command.upgrade(cfg, "head")
        logger.info("Alembic migrations applied (postgres)")
        return

    from app.models import (  # noqa: F401
        application,
        draft,
        job,
        match,
        profile,
        scrape_run,
    )

    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized")

    # Simple column migrations for pre-existing databases (TalentHive pattern)
    from sqlalchemy import text as sa_text

    new_columns = [
        ("profiles", "onboarded", "INTEGER DEFAULT 0"),
        ("profiles", "country", "VARCHAR(2)"),
        ("profiles", "region", "VARCHAR(255)"),
        ("profiles", "municipality", "VARCHAR(255)"),
        ("profiles", "remote_only", "INTEGER DEFAULT 0"),
        ("profiles", "search_queries", "TEXT"),
        ("profiles", "languages", "TEXT"),
        ("profiles", "include_remote", "INTEGER DEFAULT 0"),
        ("job_postings", "dedupe_key", "VARCHAR(16)"),
        ("applications", "draft_id", "INTEGER"),
    ]
    try:
        with engine.connect() as conn:
            for table, column, ddl in new_columns:
                try:
                    conn.execute(sa_text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                    conn.commit()
                    logger.info("Added %s.%s column", table, column)
                except Exception:
                    pass  # Column already exists
    except Exception as e:
        logger.warning("Migration check: %s", e)

    # Backfill dedupe keys for pre-existing rows, and dismiss older copies
    # of cross-board duplicates that were never matched
    from app.core.dedupe import dedupe_key_for

    try:
        from sqlalchemy import text as t

        with engine.connect() as conn:
            rows = conn.execute(t("SELECT id, title, company, location FROM job_postings")).fetchall()
            seen: dict[str, int] = {}
            dismiss: list[int] = []
            for job_id, title, company, location in rows:
                conn.execute(
                    t("UPDATE job_postings SET dedupe_key = :k WHERE id = :i"),
                    {"k": dedupe_key_for(title, company, location), "i": job_id},
                )
            # Re-read with statuses; keep the NEWEST copy of each key
            rows = conn.execute(
                t("SELECT id, dedupe_key, status, scraped_at FROM job_postings ORDER BY scraped_at DESC")
            ).fetchall()
            for job_id, key, status, _scraped in rows:
                if status != "new" or not key:
                    continue
                if key in seen:
                    dismiss.append(job_id)
                else:
                    seen[key] = job_id
            for job_id in dismiss:
                conn.execute(
                    t("UPDATE job_postings SET status = 'dismissed' WHERE id = :i"), {"i": job_id}
                )
            conn.commit()
            if dismiss:
                logger.info("Dedupe backfill: dismissed %d older duplicate postings", len(dismiss))
    except Exception as e:
        logger.warning("Dedupe backfill skipped: %s", e)
