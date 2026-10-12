"""Authenticated product identity API for Onsite48."""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import timedelta
from typing import Any, Literal, Optional
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import CONFIG
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    get_db_session,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models import OAuthAccount, Organization, OrganizationMember, User, UserRole, UserSession
from app.services.connector_vault import get_cipher

logger = structlog.get_logger("onsite48.api.auth")
router = APIRouter(prefix="/api/auth", tags=["authentication"])
REFRESH_COOKIE_NAME = "onsite48_refresh"


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=72)
    display_name: str = Field(min_length=2, max_length=120)
    organization_name: str = Field(min_length=2, max_length=160)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    display_name: str
    avatar_url: Optional[str] = None
    organization_id: str
    organization_name: str
    role: UserRole


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserResponse


class OAuthStartResponse(BaseModel):
    authorization_url: str
    redirect_uri: str


class OAuthDiagnosticsResponse(BaseModel):
    provider: str
    redirect_uri: str
    frontend_url: str
    configured: bool


OAUTH_PROVIDERS: dict[str, dict[str, Any]] = {
    "google": {
        "client_id": lambda: __import__("os").environ.get("GOOGLE_CLIENT_ID"),
        "client_secret": lambda: __import__("os").environ.get("GOOGLE_CLIENT_SECRET"),
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "profile_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scopes": ["openid", "email", "profile"],
    },
    "github": {
        "client_id": lambda: __import__("os").environ.get("GITHUB_CLIENT_ID"),
        "client_secret": lambda: __import__("os").environ.get("GITHUB_CLIENT_SECRET"),
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "profile_url": "https://api.github.com/user",
        "scopes": ["read:user", "user:email"],
    },
}


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:130] or "workspace"


async def create_unique_organization(session: AsyncSession, organization_name: str) -> Organization:
    base_slug = normalize_slug(organization_name)
    slug = base_slug
    suffix = 2
    while await session.scalar(select(Organization.id).where(Organization.slug == slug)):
        slug = f"{base_slug[:120]}-{suffix}"
        suffix += 1
    organization = Organization(name=organization_name.strip(), slug=slug)
    session.add(organization)
    await session.flush()
    return organization


async def default_membership(session: AsyncSession, user: User) -> OrganizationMember:
    membership = await session.scalar(
        select(OrganizationMember)
        .where(OrganizationMember.user_id == user.id)
        .order_by(OrganizationMember.created_at.asc())
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active workspace is associated with this account.")
    return membership


async def issue_auth_response(
    response: Response,
    session: AsyncSession,
    user: User,
    request: Request,
) -> AuthResponse:
    membership = await default_membership(session, user)
    organization = await session.get(Organization, membership.organization_id)
    if organization is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Workspace configuration is incomplete.")

    expires_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc) + timedelta(days=CONFIG.security.refresh_token_expire_days)
    user_session = UserSession(
        user_id=user.id,
        refresh_token_hash="pending",
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent", "")[:512] or None,
        ip_address=request.client.host if request.client else None,
    )
    session.add(user_session)
    await session.flush()
    refresh_token = create_refresh_token(user_session.id, user.id)
    user_session.refresh_token_hash = hash_refresh_token(refresh_token)
    access_token = create_access_token(user, membership.organization_id, membership.role)

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=CONFIG.security.cookie_secure,
        samesite="lax",
        domain=CONFIG.security.cookie_domain,
        max_age=CONFIG.security.refresh_token_expire_days * 24 * 60 * 60,
        path="/api/auth",
    )
    return AuthResponse(
        access_token=access_token,
        expires_in=CONFIG.security.access_token_expire_minutes * 60,
        user=UserResponse(
            id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            organization_id=str(organization.id),
            organization_name=organization.name,
            role=membership.role,
        ),
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    response: Response,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    email = str(payload.email).lower()
    if await session.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account already exists for this email address.")

    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    user = User(email=email, display_name=payload.display_name.strip(), password_hash=password_hash)
    session.add(user)
    await session.flush()
    organization = await create_unique_organization(session, payload.organization_name)
    session.add(OrganizationMember(organization_id=organization.id, user_id=user.id, role=UserRole.OWNER))
    await session.flush()
    logger.info("auth.user_registered", user_id=str(user.id), organization_id=str(organization.id))
    return await issue_auth_response(response, session, user, request)


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    user = await session.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password.")
    return await issue_auth_response(response, session, user, request)


@router.post("/refresh", response_model=AuthResponse)
async def refresh_session(
    response: Response,
    request: Request,
    refresh_token: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    session: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session has expired. Please sign in again.")
    payload = decode_token(refresh_token, "refresh")
    try:
        session_id = uuid.UUID(payload["sid"])
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session token.") from exc
    stored = await session.get(UserSession, session_id)
    if (
        stored is None
        or stored.user_id != user_id
        or stored.revoked_at is not None
        or stored.expires_at <= __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        or not secrets.compare_digest(stored.refresh_token_hash, hash_refresh_token(refresh_token))
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session has expired. Please sign in again.")
    stored.revoked_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found or disabled.")
    return await issue_auth_response(response, session, user, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    if refresh_token:
        try:
            payload = decode_token(refresh_token, "refresh")
            session_id = uuid.UUID(payload["sid"])
            stored = await session.get(UserSession, session_id)
            if stored:
                stored.revoked_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        except (HTTPException, KeyError, ValueError):
            pass
    response.delete_cookie(key=REFRESH_COOKIE_NAME, domain=CONFIG.security.cookie_domain, path="/api/auth")
    return response


@router.get("/me", response_model=UserResponse)
async def current_user_profile(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    membership = await default_membership(session, user)
    organization = await session.get(Organization, membership.organization_id)
    assert organization is not None
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        organization_id=str(organization.id),
        organization_name=organization.name,
        role=membership.role,
    )


@router.get("/oauth/{provider}/diagnostics", response_model=OAuthDiagnosticsResponse)
async def oauth_diagnostics(provider: str) -> OAuthDiagnosticsResponse:
    config = OAUTH_PROVIDERS.get(provider)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unsupported sign-in provider.")
    redirect_uri = f"{CONFIG.frontend_url}/api/auth/oauth/{provider}/callback"
    return OAuthDiagnosticsResponse(
        provider=provider,
        redirect_uri=redirect_uri,
        frontend_url=CONFIG.frontend_url,
        configured=bool(config["client_id"]() and config["client_secret"]()),
    )


@router.get("/oauth/{provider}/start", response_model=OAuthStartResponse)
async def oauth_start(
    provider: str,
    request: Request,
    return_to: str = Query(default="/"),
):
    config = OAUTH_PROVIDERS.get(provider)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unsupported sign-in provider.")
    client_id = config["client_id"]()
    if not client_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"{provider.title()} sign-in is not configured.")
    if not return_to.startswith("/") or return_to.startswith("//"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid return destination.")
    state = secrets.token_urlsafe(32)
    redirect_uri = f"{CONFIG.frontend_url}/api/auth/oauth/{provider}/callback"
    state_payload = json.dumps({"provider": provider, "return_to": return_to, "redirect_uri": redirect_uri})
    redis_client = request.app.state.redis_client
    await redis_client.setex(f"onsite48:auth:oauth:{state}", 600, state_payload)
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(config["scopes"]),
        "state": state,
    }
    if provider == "google":
        query.update({"access_type": "offline", "prompt": "select_account"})
    return OAuthStartResponse(
        authorization_url=f"{config['authorize_url']}?{urlencode(query)}",
        redirect_uri=redirect_uri,
    )


async def fetch_oauth_profile(provider: str, code: str, redirect_uri: str) -> tuple[dict[str, Any], dict[str, Any]]:
    config = OAUTH_PROVIDERS[provider]
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            config["token_url"],
            data={
                "client_id": config["client_id"](),
                "client_secret": config["client_secret"](),
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        token_response.raise_for_status()
        tokens = token_response.json()
        profile_response = await client.get(
            config["profile_url"],
            headers={"Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json"},
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
        if provider == "github" and not profile.get("email"):
            email_response = await client.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json"},
            )
            email_response.raise_for_status()
            primary = next((item for item in email_response.json() if item.get("primary") and item.get("verified")), None)
            profile["email"] = primary.get("email") if primary else None
    return tokens, profile


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_db_session),
):
    raw_state = await request.app.state.redis_client.getdel(f"onsite48:auth:oauth:{state}")
    if not raw_state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This sign-in request has expired or is invalid.")
    state_data = json.loads(raw_state)
    if state_data.get("provider") != provider:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sign-in provider mismatch.")
    try:
        tokens, profile = await fetch_oauth_profile(provider, code, state_data["redirect_uri"])
    except httpx.HTTPError as exc:
        logger.warning("auth.oauth_exchange_failed", provider=provider, error=str(exc))
        return RedirectResponse(f"{CONFIG.frontend_url}/auth/callback?status=error&provider={provider}", status_code=302)

    provider_account_id = str(profile.get("sub") or profile.get("id") or "")
    email = str(profile.get("email") or "").strip().lower()
    if not provider_account_id or not email:
        return RedirectResponse(f"{CONFIG.frontend_url}/auth/callback?status=error&provider={provider}", status_code=302)

    oauth_account = await session.scalar(
        select(OAuthAccount).where(OAuthAccount.provider == provider, OAuthAccount.provider_account_id == provider_account_id)
    )
    if oauth_account:
        user = await session.get(User, oauth_account.user_id)
    else:
        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            display_name = str(profile.get("name") or profile.get("login") or email.split("@", 1)[0])[:120]
            user = User(email=email, display_name=display_name, avatar_url=profile.get("picture") or profile.get("avatar_url"))
            session.add(user)
            await session.flush()
            organization = await create_unique_organization(session, f"{display_name}'s workspace")
            session.add(OrganizationMember(organization_id=organization.id, user_id=user.id, role=UserRole.OWNER))
            await session.flush()
        oauth_account = OAuthAccount(user_id=user.id, provider=provider, provider_account_id=provider_account_id)
        session.add(oauth_account)

    cipher = get_cipher()
    oauth_account.encrypted_access_token = cipher.encrypt(tokens["access_token"].encode()).decode()
    if tokens.get("refresh_token"):
        oauth_account.encrypted_refresh_token = cipher.encrypt(tokens["refresh_token"].encode()).decode()
    await session.flush()

    callback_response = RedirectResponse(
        f"{CONFIG.frontend_url}/auth/callback?status=success&provider={provider}&return_to={state_data['return_to']}",
        status_code=302,
    )
    # OAuth redirect creates a normal Onsite48 session.
    await issue_auth_response(callback_response, session, user, request)
    return callback_response