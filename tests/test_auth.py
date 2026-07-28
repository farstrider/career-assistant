import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import SecretStr
from redis.exceptions import RedisError
from starlette.requests import Request

from career_assistant.auth import (
    check_login_throttle,
    normalize_password,
    normalize_username,
    set_session_cookie,
    token_digest,
)
from career_assistant.models import AuthSession, uuid7
from career_assistant.settings import AppSettings, DatabaseSettings, RedisSettings, Settings


def test_password_normalization_and_boundaries() -> None:
    assert normalize_password("e\u0301" * 15) == "é" * 15
    assert normalize_password(" leading spaces preserved") == " leading spaces preserved"
    with pytest.raises(HTTPException) as error:
        normalize_password("too short")
    assert error.value.detail["code"] == "INVALID_PASSWORD"


def test_username_is_casefolded_and_control_characters_are_rejected() -> None:
    assert normalize_username("  Straße ") == "strasse"
    with pytest.raises(HTTPException):
        normalize_username("user\nname")


def test_session_digest_and_uuid7_shape() -> None:
    assert (
        token_digest("opaque")
        == "6d229884c1268bb0ab32d8da315d0fe52f9147228bd830a37bc9fb28a954940d"  # pragma: allowlist secret  # noqa: E501
    )
    generated = uuid7()
    assert generated.version == 7
    assert generated.variant == uuid.RFC_4122


def test_session_cookie_uses_browser_security_defaults() -> None:
    now = datetime.now(UTC)
    session = AuthSession(
        token_digest="0" * 64,
        csrf_secret="csrf",  # pragma: allowlist secret
        created_at=now,
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=12),
        absolute_expires_at=now + timedelta(days=7),
    )
    settings = Settings(
        app=AppSettings(base_url="https://10.0.0.2:8443", bind_address="10.0.0.2"),
        database=DatabaseSettings(url=SecretStr("postgresql+psycopg://u:p@db/app")),
        redis=RedisSettings(url=SecretStr("redis://redis/0")),
    )
    response = Response()
    set_session_cookie(response, "raw-token", session, settings)
    cookie = response.headers["set-cookie"]
    assert "career_session=raw-token" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie


@pytest.mark.asyncio
async def test_new_logins_fail_closed_when_throttle_storage_is_unavailable() -> None:
    class FailingRedis:
        async def eval(self, *_: object) -> None:
            raise RedisError("unavailable")

    settings = Settings(
        app=AppSettings(base_url="https://10.0.0.2:8443", bind_address="10.0.0.2"),
        database=DatabaseSettings(url=SecretStr("postgresql+psycopg://u:p@db/app")),
        redis=RedisSettings(url=SecretStr("redis://redis/0")),
    )
    app = SimpleNamespace(
        state=SimpleNamespace(settings=settings, services=SimpleNamespace(redis=FailingRedis()))
    )
    request = Request(
        {
            "type": "http",
            "app": app,
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": ("10.0.0.9", 1234),
        }
    )
    with pytest.raises(HTTPException) as error:
        await check_login_throttle(request, "member")
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "LOGIN_UNAVAILABLE"
