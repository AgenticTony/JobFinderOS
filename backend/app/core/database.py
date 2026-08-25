"""
Database configuration for JobFinderOS.

Follows the TalentHive pattern (engine + SessionLocal + get_db + init_db)
but defaults to SQLite so the app runs with zero external services.
Set DATABASE_URL to PostgreSQL for production.
"""

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./jobfinderos.db")

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

Base = declarative_base()


def get_db():
    """FastAPI dependency yielding a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables on startup (TalentHive pattern) + light migrations."""
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
