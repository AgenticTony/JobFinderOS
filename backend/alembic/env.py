from logging.config import fileConfig

from sqlalchemy import engine_from_config, exc, pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# URL precedence (DATA-4): a URL the CALLER injected into this Config
# (init_db sets sqlalchemy.url from app settings = real env vars MERGED
# with backend/.env) wins; the DATABASE_URL env var is consulted only
# when the config carries no URL — the `alembic upgrade head` CLI shape
# (CI's minimal-env step, the migration container). env.py used to let
# the env var override unconditionally, so a boot whose DATABASE_URL
# lived only in backend/.env silently migrated a fresh sqlite file.
# Never from alembic.ini (it defines no URL — migration_env tests pin
# that). Policy + normalization live in the dependency-free
# app.core.migration_env: importing app.core.database here would
# construct Settings()/engines — a migration step carrying only
# DATABASE_URL (pre-deploy command, init container) must not need full
# app config.
from app.core.migration_env import (  # noqa: E402
    MIGRATION_LOCK_TIMEOUT_SECONDS,
    register_migration_timeouts,
    resolve_url,
)

config.set_main_option("sqlalchemy.url", resolve_url(config))

# Interpret the config file for Python logging.
# This line sets up loggers basically.
# disable_existing_loggers=False (the default True) is load-bearing: by the
# time alembic runs inside the app (init_db at boot, the TestClient lifespan
# in tests) every app.* logger already exists, and fileConfig's default
# DISABLES them — the entire application went silent after the first
# migration run (P1-5a's mandated cleanup warning was one of the swallowed
# ones; nothing from app.* reached stdout/stderr or caplog).
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# JobFinderOS models — importing the package registers every table
from app import models  # noqa: E402, F401
from app.core.orm import Base  # noqa: E402

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
    # DATA-6: bound DDL lock WAITING on the migration connection itself
    # (the advisory lock in init_db bounds only its own connection).
    # No-op off postgres. lock_timeout only — see migration_env for why
    # there is deliberately no statement_timeout.
    connectable = register_migration_timeouts(
        engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        try:
            with context.begin_transaction():
                context.run_migrations()
        except exc.OperationalError as err:
            # A lock_timeout abort already says so in the driver message,
            # but naming the knob and its value turns "why did the deploy
            # die?" into a one-line diagnosis.
            if "lock timeout" in str(err).lower():
                raise RuntimeError(
                    f"Migration aborted: DDL waited more than "
                    f"{MIGRATION_LOCK_TIMEOUT_SECONDS}s for a lock "
                    "(lock_timeout set on the alembic connection — "
                    "DATA-6). A long-running transaction is holding "
                    "locks on the target tables; let it finish or "
                    "terminate it, then retry the deploy."
                ) from err
            raise


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
