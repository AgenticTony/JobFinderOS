"""The declarative Base — dependency-free by design.

alembic's env.py imports this (with app.core.dburl) so the migration
runner registers every table WITHOUT importing app.core.database, which
instantiates Settings() at module scope and would require full app
config (AUTH_SECRET production guards) for a step that conventionally
carries only DATABASE_URL. app.core.database re-exports Base for the
existing public surface.
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()
