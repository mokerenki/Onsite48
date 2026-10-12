"""One-time recovery for unversioned legacy Onsite48 databases.

This module is intentionally not a general migration mechanism. It exists for
installations created by the pre-Alembic metadata bootstrap, where PostgreSQL
objects survive but `alembic_version` is absent or empty. Normal deployments
must continue to use the ordered Alembic migration chain.
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.schema import MetaData


VERSION_TABLE = "alembic_version"
LEGACY_MARKER_TABLES = frozenset(
    {
        "users",
        "organizations",
        "organization_members",
        "user_sessions",
        "task_threads",
        "connector_connections",
    }
)


class LegacySchemaRecoveryError(RuntimeError):
    """Raised when a partial legacy schema cannot safely be marked current."""


def _existing_version_rows(connection: Connection) -> list[object] | None:
    inspector = sa.inspect(connection)
    if not inspector.has_table(VERSION_TABLE):
        return None
    return list(connection.execute(sa.text(f"SELECT version_num FROM {VERSION_TABLE}")))


def is_unversioned_legacy_schema(connection: Connection) -> bool:
    """Return true only for a database with product tables but no revision row."""
    version_rows = _existing_version_rows(connection)
    if version_rows:
        return False
    existing_tables = set(sa.inspect(connection).get_table_names())
    return bool(existing_tables & LEGACY_MARKER_TABLES)


def _schema_gaps(connection: Connection, metadata: MetaData) -> tuple[list[str], list[str]]:
    inspector = sa.inspect(connection)
    actual_tables = set(inspector.get_table_names())
    missing_tables = sorted(table.name for table in metadata.sorted_tables if table.name not in actual_tables)
    missing_columns: list[str] = []
    for table in metadata.sorted_tables:
        if table.name not in actual_tables:
            continue
        actual_columns = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in actual_columns:
                missing_columns.append(f"{table.name}.{column.name}")
    return missing_tables, sorted(missing_columns)


def reconcile_unversioned_legacy_schema(
    connection: Connection,
    metadata: MetaData,
    head_revision: str,
) -> bool:
    """Reconcile a known legacy bootstrap and record its verified schema head.

    The operation is safe to invoke on every process start because it is a no-op
    for any database that already has an Alembic revision row or no Onsite48
    legacy marker tables. On the narrowly identified legacy state, `create_all`
    creates *only* missing objects and never drops or alters existing data.
    The revision is recorded only after every ORM table and column is present.
    """
    if not is_unversioned_legacy_schema(connection):
        return False

    metadata.create_all(bind=connection, checkfirst=True)
    missing_tables, missing_columns = _schema_gaps(connection, metadata)
    if missing_tables or missing_columns:
        detail = "; ".join(
            item
            for item in (
                f"missing tables: {', '.join(missing_tables)}" if missing_tables else "",
                f"missing columns: {', '.join(missing_columns)}" if missing_columns else "",
            )
            if item
        )
        raise LegacySchemaRecoveryError(
            "Legacy schema recovery did not reach the current model contract; "
            f"refusing to mark migrations complete ({detail})."
        )

    inspector = sa.inspect(connection)
    if not inspector.has_table(VERSION_TABLE):
        connection.execute(
            sa.text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
    connection.execute(sa.text("DELETE FROM alembic_version"))
    connection.execute(
        sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
        {"revision": head_revision},
    )
    return True


__all__: Iterable[str] = (
    "LegacySchemaRecoveryError",
    "is_unversioned_legacy_schema",
    "reconcile_unversioned_legacy_schema",
)