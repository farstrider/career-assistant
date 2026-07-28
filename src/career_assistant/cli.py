from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from career_assistant.auth import (
    normalize_username,
    password_hash,
    revoke_user_sessions,
    temporary_password,
)
from career_assistant.models import AppUser, Profile
from career_assistant.services import Services
from career_assistant.settings import load_settings


async def bootstrap_admin(username: str | None, display_name: str | None) -> None:
    settings = load_settings()
    services = Services.create(settings)
    try:
        async with services.sessions() as database:
            if await database.scalar(select(func.count()).select_from(AppUser)):
                raise SystemExit("bootstrap refused: an account already exists")
            normalized = normalize_username(username or input("Username: "))
            display = (display_name or input("Display name: ")).strip()
            if not display:
                raise SystemExit("display name is required")
            generated = temporary_password()
            user = AppUser(
                username=normalized,
                display_name=display,
                password_hash=password_hash.hash(generated),
                is_admin=True,
                is_active=True,
                must_change_password=True,
            )
            database.add(user)
            await database.flush()
            database.add(
                Profile(
                    user_id=user.id,
                    locale=settings.app.locale,
                    timezone=settings.app.timezone,
                )
            )
            await database.commit()
            print(f"Temporary password (shown once): {generated}")
    finally:
        await services.close()


async def reset_password(username: str) -> None:
    services = Services.create(load_settings())
    try:
        async with services.sessions() as database:
            user = await database.scalar(
                select(AppUser).where(AppUser.username == normalize_username(username))
            )
            if user is None:
                raise SystemExit("account not found")
            confirmation = input("Type RESET to confirm password reset: ")
            if confirmation != "RESET":
                raise SystemExit("reset cancelled")
            generated = temporary_password()
            user.password_hash = password_hash.hash(generated)
            user.must_change_password = True
            await revoke_user_sessions(database, user.id, "cli_password_reset")
            await database.commit()
            print(f"Temporary password (shown once): {generated}")
    finally:
        await services.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="career")
    auth = root.add_subparsers(dest="group", required=True).add_parser("auth")
    commands = auth.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin")
    bootstrap.add_argument("--username")
    bootstrap.add_argument("--display-name")
    reset = commands.add_parser("reset-password")
    reset.add_argument("username")
    return root


def main() -> None:
    arguments = parser().parse_args()
    if arguments.command == "bootstrap-admin":
        asyncio.run(bootstrap_admin(arguments.username, arguments.display_name))
    else:
        asyncio.run(reset_password(arguments.username))
