"""Encrypted storage helpers for user-owned connector credentials."""

from __future__ import annotations

import json
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import CONFIG
from app.models import ConnectorConnection, ConnectorStatus

# A process-local key is acceptable only for development. Production validation
# requires CONNECTOR_ENCRYPTION_KEY, making tokens stable across restarts.
_DEVELOPMENT_KEY = Fernet.generate_key()


def get_cipher() -> Fernet:
    key = CONFIG.security.credential_encryption_key.encode() if CONFIG.security.credential_encryption_key else _DEVELOPMENT_KEY
    return Fernet(key)


def encrypt_credentials(credentials: dict[str, Any]) -> str:
    return get_cipher().encrypt(json.dumps(credentials).encode("utf-8")).decode("utf-8")


def decrypt_credentials(encrypted_credentials: Optional[str]) -> Optional[dict[str, Any]]:
    if not encrypted_credentials:
        return None
    try:
        return json.loads(get_cipher().decrypt(encrypted_credentials.encode("utf-8")))
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return None


async def get_user_connector_credentials(
    session: AsyncSession,
    *,
    organization_id: Any,
    user_id: Any,
    connector_id: str,
) -> Optional[dict[str, Any]]:
    connection = await session.scalar(
        select(ConnectorConnection).where(
            ConnectorConnection.organization_id == organization_id,
            ConnectorConnection.user_id == user_id,
            ConnectorConnection.connector_id == connector_id,
            ConnectorConnection.status == ConnectorStatus.CONNECTED,
        )
    )
    return decrypt_credentials(connection.encrypted_credentials) if connection else None