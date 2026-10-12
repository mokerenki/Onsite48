"""Authentication, session, and authorization utilities for Onsite48."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import CONFIG
from app.db import get_db_session
from app.models import OrganizationMember, User, UserRole, UserSession

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password must be 72 bytes or fewer.")
    return password_context.hash(password)


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    return bool(password_hash) and password_context.verify(password, password_hash)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _encode_token(payload: dict[str, Any], expires_delta: timedelta) -> str:
    now = utcnow()
    claims = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "iss": "onsite48",
    }
    return jwt.encode(claims, CONFIG.security.jwt_secret, algorithm=CONFIG.security.jwt_algorithm)


def create_access_token(user: User, organization_id: uuid.UUID, role: UserRole) -> str:
    return _encode_token(
        {
            "sub": str(user.id),
            "email": user.email,
            "organization_id": str(organization_id),
            "role": role.value,
            "type": ACCESS_TOKEN_TYPE,
            "jti": secrets.token_urlsafe(16),
        },
        timedelta(minutes=CONFIG.security.access_token_expire_minutes),
    )


def create_refresh_token(session_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return _encode_token(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "type": REFRESH_TOKEN_TYPE,
            "jti": secrets.token_urlsafe(24),
        },
        timedelta(days=CONFIG.security.refresh_token_expire_days),
    )


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            CONFIG.security.jwt_secret,
            algorithms=[CONFIG.security.jwt_algorithm],
            issuer="onsite48",
        )
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session is invalid or has expired.") from exc
    if payload.get("type") != expected_type or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
    return payload


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication is required.")
    payload = decode_token(credentials.credentials, ACCESS_TOKEN_TYPE)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identity.") from exc
    user = await session.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found or disabled.")
    return user


async def get_current_user_from_token(token: str, session: AsyncSession) -> User:
    payload = decode_token(token, ACCESS_TOKEN_TYPE)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user identity.") from exc
    user = await session.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found or disabled.")
    return user


def websocket_access_token(websocket: WebSocket) -> Optional[str]:
    """Return a WebSocket access token without putting it in a loggable URL.

    Browser WebSockets cannot send an Authorization header. The supported
    client therefore offers ``onsite48.v1`` followed by the JWT as WebSocket
    subprotocols. Reverse-proxy access logs record the request path but not
    the Sec-WebSocket-Protocol header. Query-string support remains temporary
    compatibility for clients released before the subprotocol protocol.
    """
    offered = [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",") if value.strip()]
    try:
        protocol_index = offered.index("onsite48.v1")
    except ValueError:
        protocol_index = -1
    if protocol_index >= 0 and protocol_index + 1 < len(offered):
        return offered[protocol_index + 1]
    return websocket.query_params.get("token")


async def get_websocket_user(websocket: WebSocket, session: AsyncSession) -> User:
    """Authenticate a WebSocket with a short-lived access token."""
    token = websocket_access_token(websocket)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="WebSocket authentication is required.")
    return await get_current_user_from_token(token, session)


async def get_membership(
    user: User,
    organization_id: uuid.UUID,
    session: AsyncSession,
) -> OrganizationMember:
    membership = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.user_id == user.id,
            OrganizationMember.organization_id == organization_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have access to this organization.")
    return membership


def require_role(*roles: UserRole):
    async def role_checker(
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_db_session),
    ) -> User:
        memberships = (await session.scalars(select(OrganizationMember).where(OrganizationMember.user_id == user.id))).all()
        if not any(membership.role in roles for membership in memberships):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to perform this action.")
        return user
    return role_checker


__all__ = [
    "ACCESS_TOKEN_TYPE",
    "REFRESH_TOKEN_TYPE",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_current_user",
    "get_current_user_from_token",
    "get_db_session",
    "get_membership",
    "get_websocket_user",
    "websocket_access_token",
    "hash_password",
    "hash_refresh_token",
    "require_role",
    "verify_password",
]