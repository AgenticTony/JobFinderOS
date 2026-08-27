from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# URL comes from the environment (DATABASE_URL), never from alembic.ini —
# sync drivers only here (alembic runs sync engines): translate async forms.
import os  # noqa: E402

_url = os.getenv("DATABASE_URL", "sqlite:///./jobfinderos.db")
# ONE normalization path for every engine (WO-11 review): the app's
# normalize_postgres_url fixes bare postgresql:// → psycopg2 (not
# installed); the async-driver step-downs stay local to alembic.
from app.core.database import normalize_postgres_url
_url = normalize_postgres_url(_url)
_url = _url.replace("sqlite+aiosqlite://", "sqlite://", 1)
config.set_main_option("sqlalchemy.url", _url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# JobFinderOS models — importing the package registers every table
from app import models  # noqa: E402, F401
from app.core.database import Base  # noqa: E402

target_metadata = Base.metadata


def _render_item(type_, obj, autogen_context):
    """Render fastapi-users' GUID as SQLAlchemy's portable Uuid type."""
    from fastapi_users_db_sqlalchemy.generics import GUID

    if type_ == "type" and isinstance(obj, GUID):

        return "sa.Uuid()"
    return False  # fall through to default rendering

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
