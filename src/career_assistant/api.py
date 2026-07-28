from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import RequestResponseEndpoint

from career_assistant.auth import (
    Admin,
    AdminMutation,
    AdminUserResponse,
    CreateUserRequest,
    Current,
    Database,
    LoginRequest,
    MessageResponse,
    Mutation,
    PasswordRequest,
    SessionResponse,
    TemporaryPasswordResponse,
    UpdateUserRequest,
    admin_user_response,
    authenticate_login,
    create_session,
    ensure_admin_remains,
    normalize_password,
    normalize_username,
    password_hash,
    problem,
    revoke_user_sessions,
    session_response,
    set_session_cookie,
    temporary_password,
)
from career_assistant.logging import configure_logging
from career_assistant.models import AppUser, Profile
from career_assistant.services import Services
from career_assistant.settings import Settings, load_settings

logger = logging.getLogger(__name__)


class Health(BaseModel):
    status: Literal["ok"] = "ok"


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    configure_logging(app_settings.app.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.services = Services.create(app_settings)
        yield
        await app.state.services.close()

    app = FastAPI(
        title="Career Assistant API",
        version="0.1.0",
        openapi_url="/api/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = app_settings

    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, error: HTTPException) -> JSONResponse:
        detail = error.detail if isinstance(error.detail, dict) else {"detail": str(error.detail)}
        return JSONResponse(
            status_code=error.status_code,
            headers={**(error.headers or {}), "Cache-Control": "no-store"},
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": detail.get("code", "REQUEST_FAILED"),
                "status": error.status_code,
                "detail": detail.get("detail", "Request failed"),
                "instance": request.url.path,
                "code": detail.get("code", "REQUEST_FAILED"),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, _: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers={"Cache-Control": "no-store"},
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "INVALID_REQUEST",
                "status": status.HTTP_422_UNPROCESSABLE_CONTENT,
                "detail": "Request validation failed",
                "instance": request.url.path,
                "code": "INVALID_REQUEST",
            },
        )

    @app.middleware("http")
    async def correlation_log(request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        logger.info(
            "http_request",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
            },
        )
        return response

    @app.get("/api/v1/health/live", response_model=Health, tags=["health"])
    async def live() -> Health:
        return Health()

    @app.get("/api/v1/health/ready", response_model=Health, tags=["health"])
    async def ready(request: Request, _: Admin) -> Health:
        try:
            await request.app.state.services.ready()
        except Exception as error:
            logger.warning("readiness_failed", extra={"error_type": type(error).__name__})
            raise problem(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "DEPENDENCIES_UNAVAILABLE",
                "Dependencies unavailable",
            ) from error
        return Health()

    @app.post("/api/v1/auth/login", response_model=SessionResponse, tags=["authentication"])
    async def login(
        request: Request,
        response: Response,
        credentials: LoginRequest,
        database: Database,
    ) -> SessionResponse:
        current, raw_token = await authenticate_login(request, database, credentials)
        set_session_cookie(response, raw_token, current.session, request.app.state.settings)
        response.headers["Cache-Control"] = "no-store"
        return session_response(current)

    @app.post(
        "/api/v1/auth/logout",
        response_model=MessageResponse,
        tags=["authentication"],
    )
    async def logout(
        request: Request, response: Response, current: Mutation, database: Database
    ) -> MessageResponse:
        current.session.revoked_at = current.session.last_seen_at
        current.session.revoked_reason = "logout"
        await database.commit()
        response.delete_cookie(
            request.app.state.settings.auth.cookie_name,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return MessageResponse()

    @app.get("/api/v1/session", response_model=SessionResponse, tags=["authentication"])
    async def session(response: Response, current: Current) -> SessionResponse:
        response.headers["Cache-Control"] = "no-store"
        return session_response(current)

    @app.post(
        "/api/v1/auth/password",
        response_model=SessionResponse,
        tags=["authentication"],
    )
    async def change_password(
        request: Request,
        response: Response,
        values: PasswordRequest,
        current: Mutation,
        database: Database,
    ) -> SessionResponse:
        if not password_hash.verify(values.current_password, current.user.password_hash):
            raise problem(
                status.HTTP_400_BAD_REQUEST,
                "CURRENT_PASSWORD_INVALID",
                "Current password was not accepted",
            )
        current.user.password_hash = password_hash.hash(normalize_password(values.new_password))
        current.user.must_change_password = False
        await revoke_user_sessions(database, current.user.id, "password_change")
        replacement, raw_token = await create_session(
            database, current.user, request.app.state.settings
        )
        await database.commit()
        current.session = replacement
        set_session_cookie(response, raw_token, replacement, request.app.state.settings)
        response.headers["Cache-Control"] = "no-store"
        return session_response(current)

    @app.get("/api/v1/admin/users", response_model=list[AdminUserResponse], tags=["administration"])
    async def list_users(_: Admin, database: Database) -> list[AdminUserResponse]:
        users = (await database.scalars(select(AppUser).order_by(AppUser.username))).all()
        return [admin_user_response(user) for user in users]

    @app.post(
        "/api/v1/admin/users",
        response_model=TemporaryPasswordResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["administration"],
    )
    async def create_user(
        response: Response,
        values: CreateUserRequest,
        current: AdminMutation,
        database: Database,
    ) -> TemporaryPasswordResponse:
        generated = temporary_password()
        user = AppUser(
            username=normalize_username(values.username),
            display_name=values.display_name.strip(),
            password_hash=password_hash.hash(generated),
            is_admin=values.is_admin,
            is_active=True,
            must_change_password=True,
            created_by=current.user.id,
        )
        database.add(user)
        try:
            await database.flush()
        except IntegrityError as error:
            await database.rollback()
            raise problem(
                status.HTTP_409_CONFLICT, "USERNAME_EXISTS", "Username already exists"
            ) from error
        database.add(
            Profile(
                user_id=user.id,
                locale=values.locale,
                timezone=values.timezone,
            )
        )
        await database.commit()
        response.headers["Cache-Control"] = "no-store"
        return TemporaryPasswordResponse(
            user=admin_user_response(user), temporary_password=generated
        )

    async def find_user(user_id: uuid.UUID, database: Database) -> AppUser:
        user = await database.get(AppUser, user_id)
        if user is None:
            raise problem(status.HTTP_404_NOT_FOUND, "USER_NOT_FOUND", "User not found")
        return user

    @app.get(
        "/api/v1/admin/users/{user_id}",
        response_model=AdminUserResponse,
        tags=["administration"],
    )
    async def get_user(user_id: uuid.UUID, _: Admin, database: Database) -> AdminUserResponse:
        return admin_user_response(await find_user(user_id, database))

    @app.patch(
        "/api/v1/admin/users/{user_id}",
        response_model=AdminUserResponse,
        tags=["administration"],
    )
    async def update_user(
        user_id: uuid.UUID,
        values: UpdateUserRequest,
        _: AdminMutation,
        database: Database,
    ) -> AdminUserResponse:
        user = await find_user(user_id, database)
        await ensure_admin_remains(database, user, values)
        if values.display_name is not None:
            user.display_name = values.display_name.strip()
        if values.is_admin is not None:
            user.is_admin = values.is_admin
        if values.is_active is not None and values.is_active != user.is_active:
            user.is_active = values.is_active
            if not values.is_active:
                await revoke_user_sessions(database, user.id, "account_disabled")
        await database.commit()
        return admin_user_response(user)

    @app.post(
        "/api/v1/admin/users/{user_id}/password-reset",
        response_model=TemporaryPasswordResponse,
        tags=["administration"],
    )
    async def reset_password(
        user_id: uuid.UUID,
        response: Response,
        _: AdminMutation,
        database: Database,
    ) -> TemporaryPasswordResponse:
        user = await find_user(user_id, database)
        generated = temporary_password()
        user.password_hash = password_hash.hash(generated)
        user.must_change_password = True
        await revoke_user_sessions(database, user.id, "password_reset")
        await database.commit()
        response.headers["Cache-Control"] = "no-store"
        return TemporaryPasswordResponse(
            user=admin_user_response(user), temporary_password=generated
        )

    @app.post(
        "/api/v1/admin/users/{user_id}/sessions/revoke",
        response_model=MessageResponse,
        tags=["administration"],
    )
    async def revoke_sessions(
        user_id: uuid.UUID, _: AdminMutation, database: Database
    ) -> MessageResponse:
        await find_user(user_id, database)
        await revoke_user_sessions(database, user_id, "administrator_revocation")
        await database.commit()
        return MessageResponse()

    return app
