from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from career_assistant.models import AppUser, AuthSession, Profile
from career_assistant.settings import Settings

password_hash = PasswordHash.recommended()
_dummy_hash = password_hash.hash("dummy-authentication-work-only")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class PasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserIdentity(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str


class SessionResponse(BaseModel):
    user: UserIdentity
    profile_id: uuid.UUID
    roles: list[Literal["member", "admin"]]
    locale: str
    timezone: str
    must_change_password: bool
    csrf_token: str


class AdminUserResponse(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    is_admin: bool
    is_active: bool
    must_change_password: bool
    created_at: datetime
    last_login_at: datetime | None


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    is_admin: bool = False
    locale: str = Field(default="en", min_length=2, max_length=32)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("display name is required")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be an IANA timezone") from error
        return value


class UpdateUserRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_admin: bool | None = None
    is_active: bool | None = None

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("display name is required")
        return value.strip() if value is not None else None


class TemporaryPasswordResponse(BaseModel):
    user: AdminUserResponse
    temporary_password: str


class MessageResponse(BaseModel):
    status: Literal["ok"] = "ok"


@dataclass
class Authenticated:
    user: AppUser
    profile: Profile
    session: AuthSession


def problem(
    status_code: int, code: str, detail: str, headers: dict[str, str] | None = None
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "detail": detail},
        headers=headers,
    )


def normalize_username(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.strip()).casefold()
    if (
        not normalized
        or len(normalized) > 128
        or any(unicodedata.category(character).startswith("C") for character in normalized)
    ):
        raise problem(status.HTTP_422_UNPROCESSABLE_CONTENT, "INVALID_USERNAME", "Invalid username")
    return normalized


def normalize_password(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if not 15 <= len(normalized) <= 128:
        raise problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "INVALID_PASSWORD",
            "Password must contain 15 to 128 Unicode code points",
        )
    return normalized


def temporary_password() -> str:
    return secrets.token_urlsafe(24)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.services.sessions() as session:
        yield session


Database = Annotated[AsyncSession, Depends(database_session)]


def require_origin(request: Request) -> None:
    expected = urlsplit(request.app.state.settings.app.base_url)
    supplied = urlsplit(request.headers.get("Origin", ""))
    if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
        raise problem(status.HTTP_403_FORBIDDEN, "INVALID_ORIGIN", "Request origin is not allowed")


async def set_profile_context(database: AsyncSession, profile_id: uuid.UUID) -> None:
    await database.execute(
        text("SELECT set_config('career.profile_id', :profile_id, true)"),
        {"profile_id": str(profile_id)},
    )


async def current_session(
    request: Request,
    database: Database,
    raw_token: Annotated[str | None, Cookie(alias="career_session")] = None,
) -> Authenticated:
    if not raw_token:
        raise problem(
            status.HTTP_401_UNAUTHORIZED, "AUTHENTICATION_REQUIRED", "Authentication required"
        )
    now = _now()
    row = (
        await database.execute(
            select(AuthSession, AppUser, Profile)
            .join(AppUser, AppUser.id == AuthSession.user_id)
            .join(Profile, Profile.user_id == AppUser.id)
            .where(AuthSession.token_digest == token_digest(raw_token))
        )
    ).one_or_none()
    if row is None:
        raise problem(
            status.HTTP_401_UNAUTHORIZED, "AUTHENTICATION_REQUIRED", "Authentication required"
        )
    session, user, profile = row
    if (
        session.revoked_at is not None
        or session.idle_expires_at <= now
        or session.absolute_expires_at <= now
        or not user.is_active
    ):
        if session.idle_expires_at <= now or session.absolute_expires_at <= now:
            await database.delete(session)
            await database.commit()
        raise problem(
            status.HTTP_401_UNAUTHORIZED, "AUTHENTICATION_REQUIRED", "Authentication required"
        )
    if user.must_change_password and request.url.path not in {
        "/api/v1/session",
        "/api/v1/auth/password",
        "/api/v1/auth/logout",
    }:
        raise problem(
            status.HTTP_403_FORBIDDEN,
            "PASSWORD_CHANGE_REQUIRED",
            "Password change required",
        )
    session.last_seen_at = now
    session.idle_expires_at = min(
        now + timedelta(seconds=request.app.state.settings.auth.idle_seconds),
        session.absolute_expires_at,
    )
    await database.commit()
    await set_profile_context(database, profile.id)
    return Authenticated(user=user, profile=profile, session=session)


Current = Annotated[Authenticated, Depends(current_session)]


async def mutation_session(
    request: Request,
    current: Current,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Authenticated:
    require_origin(request)
    if csrf_token is None or not hmac.compare_digest(csrf_token, current.session.csrf_secret):
        raise problem(status.HTTP_403_FORBIDDEN, "INVALID_CSRF_TOKEN", "Invalid CSRF token")
    return current


Mutation = Annotated[Authenticated, Depends(mutation_session)]


async def admin_session(current: Current) -> Authenticated:
    if not current.user.is_admin:
        raise problem(status.HTTP_403_FORBIDDEN, "ADMIN_REQUIRED", "Administrator role required")
    return current


Admin = Annotated[Authenticated, Depends(admin_session)]


async def admin_mutation(current: Mutation) -> Authenticated:
    if not current.user.is_admin:
        raise problem(status.HTTP_403_FORBIDDEN, "ADMIN_REQUIRED", "Administrator role required")
    return current


AdminMutation = Annotated[Authenticated, Depends(admin_mutation)]


def session_response(current: Authenticated) -> SessionResponse:
    roles: list[Literal["member", "admin"]] = ["member"]
    if current.user.is_admin:
        roles.append("admin")
    return SessionResponse(
        user=UserIdentity(
            id=current.user.id,
            username=current.user.username,
            display_name=current.user.display_name,
        ),
        profile_id=current.profile.id,
        roles=roles,
        locale=current.profile.locale,
        timezone=current.profile.timezone,
        must_change_password=current.user.must_change_password,
        csrf_token=current.session.csrf_secret,
    )


def admin_user_response(user: AppUser) -> AdminUserResponse:
    return AdminUserResponse.model_validate(user, from_attributes=True)


def set_session_cookie(
    response: Response, raw_token: str, session: AuthSession, settings: Settings
) -> None:
    max_age = max(0, int((session.absolute_expires_at - _now()).total_seconds()))
    response.set_cookie(
        settings.auth.cookie_name,
        raw_token,
        max_age=max_age,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


async def create_session(
    database: AsyncSession, user: AppUser, settings: Settings
) -> tuple[AuthSession, str]:
    now = _now()
    raw_token = secrets.token_urlsafe(32)
    session = AuthSession(
        user_id=user.id,
        token_digest=token_digest(raw_token),
        csrf_secret=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(seconds=settings.auth.idle_seconds),
        absolute_expires_at=now + timedelta(seconds=settings.auth.absolute_seconds),
    )
    database.add(session)
    await database.flush()
    return session, raw_token


_THROTTLE_SCRIPT = """
local value = redis.call('INCR', KEYS[1])
if value == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {value, redis.call('TTL', KEYS[1])}
"""

_THROTTLE_DECREMENT_SCRIPT = """
local value = redis.call('DECR', KEYS[1])
if value <= 0 then redis.call('DEL', KEYS[1]) end
return value
"""


async def check_login_throttle(request: Request, username: str) -> None:
    settings: Settings = request.app.state.settings
    address = request.headers.get("X-Real-IP") or (
        request.client.host if request.client else "unknown"
    )
    identifiers = (
        (
            "username",
            hashlib.sha256(username.encode()).hexdigest(),
            settings.auth.username_attempts,
        ),
        ("ip", hashlib.sha256(address.encode()).hexdigest(), settings.auth.ip_attempts),
    )
    try:
        for kind, identifier, limit in identifiers:
            result = await request.app.state.services.redis.eval(
                _THROTTLE_SCRIPT,
                1,
                f"auth:login:{kind}:{identifier}",
                settings.auth.throttle_window_seconds,
            )
            attempts, retry_after = int(result[0]), max(1, int(result[1]))
            if attempts > limit:
                raise problem(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "LOGIN_THROTTLED",
                    "Too many login attempts",
                    {"Retry-After": str(retry_after)},
                )
    except RedisError as error:
        raise problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "LOGIN_UNAVAILABLE",
            "Login is temporarily unavailable",
        ) from error


async def clear_login_throttle(request: Request, username: str) -> None:
    address = request.headers.get("X-Real-IP") or (
        request.client.host if request.client else "unknown"
    )
    keys = [
        f"auth:login:username:{hashlib.sha256(username.encode()).hexdigest()}",
        f"auth:login:ip:{hashlib.sha256(address.encode()).hexdigest()}",
    ]
    with suppress(RedisError):
        await request.app.state.services.redis.delete(keys[0])
        await request.app.state.services.redis.eval(_THROTTLE_DECREMENT_SCRIPT, 1, keys[1])


async def authenticate_login(
    request: Request, database: AsyncSession, credentials: LoginRequest
) -> tuple[Authenticated, str]:
    require_origin(request)
    username = normalize_username(credentials.username)
    await check_login_throttle(request, username)
    user = await database.scalar(select(AppUser).where(AppUser.username == username))
    stored_hash = user.password_hash if user is not None else _dummy_hash
    supplied_password = unicodedata.normalize("NFC", credentials.password)
    valid, updated_hash = password_hash.verify_and_update(supplied_password, stored_hash)
    if user is None or not valid or not user.is_active:
        raise problem(
            status.HTTP_401_UNAUTHORIZED,
            "AUTHENTICATION_FAILED",
            "Username or password was not accepted",
        )
    if updated_hash:
        user.password_hash = updated_hash
    user.last_login_at = _now()
    session, raw_token = await create_session(database, user, request.app.state.settings)
    profile = await database.scalar(select(Profile).where(Profile.user_id == user.id))
    assert profile is not None
    await database.commit()
    await clear_login_throttle(request, username)
    return Authenticated(user, profile, session), raw_token


async def revoke_user_sessions(database: AsyncSession, user_id: uuid.UUID, reason: str) -> None:
    await database.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason=reason)
    )


async def ensure_admin_remains(
    database: AsyncSession, user: AppUser, values: UpdateUserRequest
) -> None:
    if not user.is_admin or not user.is_active:
        return
    removes_admin = values.is_admin is False or values.is_active is False
    if not removes_admin:
        return
    active_admins = (
        (
            await database.execute(
                select(AppUser.id)
                .where(AppUser.is_admin.is_(True), AppUser.is_active.is_(True))
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if len(active_admins) <= 1:
        raise problem(
            status.HTTP_409_CONFLICT,
            "FINAL_ACTIVE_ADMIN",
            "The final active administrator cannot be disabled or demoted",
        )


async def cleanup_sessions(database: AsyncSession) -> int:
    now = _now()
    result = await database.execute(
        delete(AuthSession).where(
            (AuthSession.idle_expires_at <= now)
            | (AuthSession.absolute_expires_at <= now)
            | (AuthSession.revoked_at <= now - timedelta(days=7))
        )
    )
    await database.commit()
    return result.rowcount  # type: ignore[attr-defined, no-any-return]
