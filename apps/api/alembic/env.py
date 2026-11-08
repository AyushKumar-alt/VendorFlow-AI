import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import URL, Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401 - registers all models on Base.metadata
from alembic import context
from app.core.config import settings
from app.db.base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_target_url() -> URL:
    """Determine the database URL for migrations.

    CRITICAL SAFETY RULE:
    When running in test mode (-x db=test or ALEMBIC_DB=test), the safety guard
    ensures the target database is strictly 'vendorflow_test'.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("db") == "test" or os.environ.get("ALEMBIC_DB") == "test":
        test_url = settings.get_test_database_url()
        approved_db = os.environ.get("VENDORFLOW_APPROVED_TEST_DB", "vendorflow_test")
        if test_url.database != approved_db:
            raise RuntimeError(
                f"Test database safety guard violation: Expected '{approved_db}', got '{test_url.database}'."
            )
        if test_url.database == "vendorflow":
            raise RuntimeError(
                "CRITICAL SAFETY VIOLATION: Migration attempted access to production database 'vendorflow'!"
            )
        return test_url

    return settings.database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_target_url().render_as_string(hide_password=False)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = create_async_engine(
        get_target_url(),
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
