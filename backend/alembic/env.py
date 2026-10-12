from __future__ import annotations

import asyncio
import logging
import os
from logging.config import fileConfig

from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, pool

from app.core.config import CONFIG
from app.db import Base
from app import models  # noqa: F401 - registers model metadata
from app.services.legacy_schema_recovery import reconcile_unversioned_legacy_schema

config = context.config
logger = logging.getLogger("alembic.env")
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic uses a synchronous driver even though application traffic is async.
#
# SYNTHAI_ALEMBIC_DATABASE_URL exists because alembic.ini carries a committed
# placeholder URL, so "is sqlalchemy.url set?" can never mean "was this
# deliberately targeted?" -- honouring that would silently redirect production
# migrations to a placeholder host. The override must be explicitly opted into,
# which is what CI and the disposable-database tests do.
_override_url = os.getenv("SYNTHAI_ALEMBIC_DATABASE_URL", "").strip()
_raw_url = _override_url or CONFIG.database_url
sync_database_url = _raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
if _override_url:
    logger.info("Using SYNTHAI_ALEMBIC_DATABASE_URL for this migration run.")
config.set_main_option("sqlalchemy.url", sync_database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "pyformat"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # The pre-redesign deployment used metadata bootstrap with no durable
        # Alembic revision row. Only the explicit recovery gate may reconcile
        # that known legacy state; all normal installations keep ordered DDL.
        if os.getenv("LEGACY_SCHEMA_RECOVERY", "false").lower() == "true":
            head_revision = ScriptDirectory.from_config(config).get_current_head()
            with connection.begin():
                if reconcile_unversioned_legacy_schema(connection, target_metadata, head_revision):
                    logger.warning(
                        "Recovered unversioned legacy Onsite48 schema and recorded Alembic head %s.",
                        head_revision,
                    )
                    return
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()