import asyncio
import uuid
from datetime import UTC, datetime

from celery import Celery  # type: ignore[import-untyped]
from sqlalchemy import select

from career_assistant.artifacts import (
    ArtifactError,
    artifact_cipher,
    decrypt_artifact,
    extract_chunks,
)
from career_assistant.auth import cleanup_sessions, set_profile_context
from career_assistant.ingestion import execute_run
from career_assistant.knowledge import proposal_for_artifact
from career_assistant.models import Artifact, Operation
from career_assistant.services import Services
from career_assistant.settings import load_settings

settings = load_settings()
celery = Celery(
    "career_assistant",
    broker=settings.redis.url.get_secret_value(),  # type: ignore[union-attr]
)
celery.conf.update(
    task_default_queue=settings.redis.task_queue,
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    beat_schedule={
        "cleanup-auth-sessions": {
            "task": "career_assistant.cleanup_auth_sessions",
            "schedule": 24 * 60 * 60,
        }
    },
)


@celery.task(name="career_assistant.healthcheck")  # type: ignore[untyped-decorator]
def healthcheck() -> str:
    return "ok"


@celery.task(name="career_assistant.cleanup_auth_sessions")  # type: ignore[untyped-decorator]
def cleanup_auth_sessions() -> int:
    async def run() -> int:
        services = Services.create(settings)
        try:
            async with services.sessions() as database:
                return await cleanup_sessions(database)
        finally:
            await services.close()

    return asyncio.run(run())


@celery.task(name="career_assistant.scan_source")  # type: ignore[untyped-decorator]
def scan_source(source_id: str, run_id: str, operation_id: str) -> None:
    async def run() -> None:
        services = Services.create(settings)
        try:
            await execute_run(
                services,
                uuid.UUID(source_id),
                uuid.UUID(run_id),
                uuid.UUID(operation_id),
            )
        finally:
            await services.close()

    asyncio.run(run())


@celery.task(name="career_assistant.process_artifact")  # type: ignore[untyped-decorator]
def process_artifact(artifact_id: str, operation_id: str, profile_id: str) -> None:
    async def run() -> None:
        services = Services.create(settings)
        try:
            async with services.sessions() as database:
                profile_uuid = uuid.UUID(profile_id)
                await set_profile_context(database, profile_uuid)
                artifact = await database.scalar(
                    select(Artifact).where(
                        Artifact.id == uuid.UUID(artifact_id), Artifact.profile_id == profile_uuid
                    )
                )
                operation = await database.scalar(
                    select(Operation).where(
                        Operation.id == uuid.UUID(operation_id),
                        Operation.profile_id == profile_uuid,
                    )
                )
                if artifact is None or operation is None:
                    return
                operation.state = "running"
                operation.progress = {"percent": 25}
                artifact.processing_state = "extracting"
                await database.commit()
                await set_profile_context(database, profile_uuid)
                try:
                    content = decrypt_artifact(settings, artifact.encrypted_content)
                    chunks = extract_chunks(artifact.media_type, content)
                    operation.progress = {"percent": 50, "chunks": len(chunks)}
                    await database.commit()
                    await set_profile_context(database, profile_uuid)
                    cipher = artifact_cipher(settings)
                    operation.progress = {"percent": 75, "chunks": len(chunks)}
                    await database.commit()
                    await set_profile_context(database, profile_uuid)
                    proposals = await proposal_for_artifact(
                        database,
                        profile_id=profile_uuid,
                        artifact_id=artifact.id,
                        chunks=[(chunk.locator, chunk.text) for chunk in chunks],
                        encrypt_excerpt=cipher.encrypt,
                    )
                    artifact.processing_state = "awaiting_review" if proposals else "completed"
                    operation.state = "succeeded"
                    operation.progress = {
                        "percent": 100,
                        "chunks": len(chunks),
                        "proposals": proposals,
                    }
                    operation.updated_at = datetime.now(UTC)
                    await database.commit()
                except ArtifactError as error:
                    artifact.processing_state = (
                        "quarantined"
                        if error.code in {"ARTIFACT_TYPE_REJECTED", "ARTIFACT_PARSE_FAILED"}
                        else "failed"
                    )
                    operation.state = "failed"
                    operation.problem_code = error.code
                    operation.problem_detail = str(error)
                    await database.commit()
        finally:
            await services.close()

    asyncio.run(run())
