from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from sqlalchemy import func, select

from career_assistant.auth import (
    normalize_username,
    password_hash,
    revoke_user_sessions,
    temporary_password,
)
from career_assistant.connectors import ManualImportConnector
from career_assistant.ingestion import execute_run, policy_allows
from career_assistant.models import AppUser, ConnectorRun, Profile, Source
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


def _datetime(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    if not isinstance(value, str):
        raise SystemExit("policy dates must be RFC 3339 strings")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit(f"invalid policy date: {value}") from error


async def apply_source_policy(filename: Path) -> None:
    document = yaml.safe_load(filename.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") != 1:
        raise SystemExit("source policy must use version 1")
    entries = document.get("sources")
    if not isinstance(entries, list):
        raise SystemExit("source policy must contain a sources list")
    services = Services.create(load_settings())
    try:
        async with services.sessions() as database:
            for entry in entries:
                if not isinstance(entry, dict):
                    raise SystemExit("each source policy entry must be an object")
                key = entry.get("key")
                kind = entry.get("kind")
                method = entry.get("acquisition_method")
                if not all(isinstance(value, str) and value for value in (key, kind, method)):
                    raise SystemExit("source key, kind, and acquisition_method are required")
                assert isinstance(key, str) and isinstance(kind, str) and isinstance(method, str)
                if kind not in {"feed", "manual"}:
                    raise SystemExit(f"unsupported source kind: {kind}")
                config = entry.get("config", {})
                if not isinstance(config, dict) or set(config) - {"feed_url", "company_name"}:
                    raise SystemExit("source config contains unsupported fields")
                source = await database.scalar(select(Source).where(Source.key == key))
                if source is None:
                    source = Source(key=key, kind=kind)
                    database.add(source)
                source.kind = kind
                base_url = entry.get("base_url")
                source.base_url = base_url if isinstance(base_url, str) else None
                source.enabled = bool(entry.get("enabled", False))
                source.acquisition_method = method
                source.policy_status = str(entry.get("policy_status", "pending_review"))
                source.policy_reviewed_at = _datetime(entry.get("policy_reviewed_at"))
                source.terms_reviewed_at = _datetime(entry.get("terms_reviewed_at"))
                source.robots_reviewed_at = _datetime(entry.get("robots_reviewed_at"))
                source.next_review_at = _datetime(entry.get("next_review_at"))
                notes = entry.get("notes")
                custodian = entry.get("credential_custodian")
                source.policy_notes = notes if isinstance(notes, str) else None
                source.credential_custodian = custodian if isinstance(custodian, str) else None
                source.requests_per_minute = int(entry.get("requests_per_minute", 0))
                source.config = config
                source.version = source.version + 1 if source.id else 1
                if source.enabled:
                    try:
                        policy_allows(source)
                    except Exception as error:
                        raise SystemExit(f"source {key} cannot be enabled: {error}") from error
            await database.commit()
            print(f"Applied {len(entries)} source policies")
    finally:
        await services.close()


async def import_jobs(source_key: str, filename: Path) -> None:
    body = filename.read_bytes()
    document = json.loads(body)
    if not isinstance(document, list) or len(document) > 1000:
        raise SystemExit("manual import must be a JSON list of at most 1000 jobs")
    if not all(isinstance(item, dict) for item in document):
        raise SystemExit("every manual import item must be an object")
    services = Services.create(load_settings())
    try:
        async with services.sessions() as database:
            source = await database.scalar(select(Source).where(Source.key == source_key))
            if source is None:
                raise SystemExit("source not found")
            key = f"manual:{hashlib.sha256(body).hexdigest()}"
            run = await database.scalar(
                select(ConnectorRun).where(
                    ConnectorRun.source_id == source.id,
                    ConnectorRun.idempotency_key == key,
                )
            )
            if run is None:
                run = ConnectorRun(
                    source_id=source.id,
                    status="queued",
                    idempotency_key=key,
                    started_at=datetime.now().astimezone(),
                )
                database.add(run)
                await database.commit()
            elif run.status == "succeeded":
                print("Import already completed; no changes made")
                return
            source_id, run_id = source.id, run.id
        await execute_run(
            services,
            source_id,
            run_id,
            connector=ManualImportConnector(document, body),
        )
        async with services.sessions() as database:
            completed = await database.get(ConnectorRun, run_id)
            assert completed is not None
            if completed.status != "succeeded":
                raise SystemExit(f"manual import failed: {completed.error_code}")
            print(
                f"Imported {completed.fetched_count} jobs "
                f"({completed.new_count} new, {completed.changed_count} changed)"
            )
    finally:
        await services.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="career")
    groups = root.add_subparsers(dest="group", required=True)
    auth = groups.add_parser("auth")
    commands = auth.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-admin")
    bootstrap.add_argument("--username")
    bootstrap.add_argument("--display-name")
    reset = commands.add_parser("reset-password")
    reset.add_argument("username")
    sources = groups.add_parser("sources")
    source_commands = sources.add_subparsers(dest="command", required=True)
    apply_policy = source_commands.add_parser("apply-policy")
    apply_policy.add_argument("file", type=Path)
    import_file = source_commands.add_parser("import")
    import_file.add_argument("source_key")
    import_file.add_argument("file", type=Path)
    return root


def main() -> None:
    arguments = parser().parse_args()
    if arguments.group == "auth" and arguments.command == "bootstrap-admin":
        asyncio.run(bootstrap_admin(arguments.username, arguments.display_name))
    elif arguments.group == "auth":
        asyncio.run(reset_password(arguments.username))
    elif arguments.command == "apply-policy":
        asyncio.run(apply_source_policy(arguments.file))
    else:
        asyncio.run(import_jobs(arguments.source_key, arguments.file))
