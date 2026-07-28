from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from career_assistant.api import create_app
from career_assistant.auth import Authenticated, current_session
from career_assistant.models import AppUser, AuthSession, Profile
from career_assistant.settings import AppSettings, DatabaseSettings, RedisSettings, Settings


class ReadyServices:
    async def ready(self) -> None:
        return None


def settings() -> Settings:
    return Settings(
        app=AppSettings(base_url="https://10.0.0.2:8443", bind_address="10.0.0.2"),
        database=DatabaseSettings(url=SecretStr("postgresql+psycopg://test:test@db/test")),
        redis=RedisSettings(url=SecretStr("redis://redis/0")),
    )


@pytest.mark.asyncio
async def test_health_session_and_admin_readiness() -> None:
    app = create_app(settings())
    app.state.services = ReadyServices()

    async def unauthenticated() -> None:
        raise HTTPException(
            401, {"code": "AUTHENTICATION_REQUIRED", "detail": "Authentication required"}
        )

    app.dependency_overrides[current_session] = unauthenticated
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://10.0.0.2:8443"
    ) as client:
        assert (await client.get("/api/v1/health/live")).json() == {"status": "ok"}
        response = await client.get("/api/v1/session")
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")

    now = datetime.now(UTC)
    authenticated = Authenticated(
        AppUser(
            id=UUID("019c0000-0000-7000-8000-000000000001"),
            username="admin",
            display_name="Administrator",
            password_hash="unused",  # pragma: allowlist secret
            is_admin=True,
            is_active=True,
            must_change_password=False,
        ),
        Profile(
            id=UUID("019c0000-0000-7000-8000-000000000002"),
            user_id=UUID("019c0000-0000-7000-8000-000000000001"),
            locale="en",
            timezone="Asia/Tokyo",
        ),
        AuthSession(
            id=UUID("019c0000-0000-7000-8000-000000000003"),
            user_id=UUID("019c0000-0000-7000-8000-000000000001"),
            token_digest="0" * 64,
            csrf_secret="csrf",  # pragma: allowlist secret
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now + timedelta(hours=12),
            absolute_expires_at=now + timedelta(days=7),
        ),
    )

    async def authenticated_session() -> Authenticated:
        return authenticated

    app.dependency_overrides[current_session] = authenticated_session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://10.0.0.2:8443"
    ) as client:
        session = await client.get("/api/v1/session")
        assert session.status_code == 200
        assert session.headers["Cache-Control"] == "no-store"
        assert session.json()["roles"] == ["member", "admin"]
        assert (await client.get("/api/v1/health/ready")).json() == {"status": "ok"}
