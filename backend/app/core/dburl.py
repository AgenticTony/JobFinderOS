"""Database URL normalization — the ONE implementation every
engine-construction path routes through (app sync, app async, alembic,
CI verification, the conftest guard).

Deliberately dependency-free: the alembic migration runner imports this
(and app.core.orm) WITHOUT app.core.config — a migration step carrying
only DATABASE_URL (Render pre-deploy, k8s init container, one-off
docker run) must not need full app settings or construct engines.
"""

def normalize_postgres_url(url: str) -> str:
    """Postgres URLs arrive in four shapes: postgres:// (Render/Heroku
    convention), bare postgresql:// (by habit), postgresql+psycopg://
    (explicit), postgresql+asyncpg:// (pre-WO-11 configs). The first
    two resolve to the psycopg2 dialect in SQLAlchemy — not installed
    (psycopg 3 is the one driver) — and the last needs the removed
    asyncpg. Everything normalizes to +psycopg.
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    return url


def async_database_url(url: str) -> str:
    """Resolve the DATABASE_URL for the async auth engine (fastapi-users'
    adapter is async-only). ONE driver covers both engines (WO-11):
    postgresql+psycopg:// serves create_engine AND create_async_engine.
    sqlite -> aiosqlite stays for local/tests.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url
