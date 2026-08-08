import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from celery import Celery  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from career_assistant.artifacts import (
    ARTIFACT_PROCESSOR_VERSION,
    ArtifactError,
    artifact_cipher,
    decrypt_artifact,
    extract_chunks,
)
from career_assistant.auth import cleanup_sessions, set_profile_context
from career_assistant.ingestion import execute_run
from career_assistant.knowledge import KnowledgeError, proposal_for_artifact
from career_assistant.models import Artifact, JobVersion, Operation, Profile
from career_assistant.reasoning import enrich_job_version
from career_assistant.services import Services
from career_assistant.settings import load_settings

settings = load_settings()
logger = logging.getLogger(__name__)
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
        },
        "recover-queued-artifact-operations": {
            "task": "career_assistant.recover_queued_artifact_operations",
            "schedule": 60,
        },
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


@celery.task(
    name="career_assistant.enrich_job",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)  # type: ignore[untyped-decorator]
def enrich_job(job_version_id: str) -> None:
    async def run() -> None:
        services = Services.create(settings)
        try:
            async with services.sessions() as database:
                version = await database.get(JobVersion, uuid.UUID(job_version_id))
                if version is not None:
                    await enrich_job_version(database, settings, version)
        finally:
            await services.close()

    asyncio.run(run())


async def _mark_artifact_failed(
    database: AsyncSession,
    profile_id: uuid.UUID,
    artifact_id: uuid.UUID,
    operation_id: uuid.UUID,
    code: str,
    detail: str,
) -> None:
    try:
        await database.rollback()
        await set_profile_context(database, profile_id)
        artifact = await database.scalar(
            select(Artifact).where(Artifact.id == artifact_id, Artifact.profile_id == profile_id)
        )
        operation = await database.scalar(
            select(Operation).where(
                Operation.id == operation_id, Operation.profile_id == profile_id
            )
        )
        if artifact is None or operation is None:
            return
        artifact.processing_state = (
            "quarantined"
            if code in {"ARTIFACT_TYPE_REJECTED", "ARTIFACT_PARSE_FAILED"}
            else "failed"
        )
        operation.state = "failed"
        operation.problem_code = code
        operation.problem_detail = detail
        await database.commit()
    except Exception:
        logger.exception(
            "artifact_failure_recording_failed", extra={"artifact_id": str(artifact_id)}
        )


@celery.task(
    name="career_assistant.process_artifact",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)  # type: ignore[untyped-decorator]
def process_artifact(artifact_id: str, operation_id: str, profile_id: str) -> None:
    async def run() -> None:
        services = Services.create(settings)
        try:
            async with services.sessions() as database:
                profile_uuid = uuid.UUID(profile_id)
                await set_profile_context(database, profile_uuid)
                operation = await database.scalar(
                    select(Operation)
                    .where(
                        Operation.id == uuid.UUID(operation_id),
                        Operation.profile_id == profile_uuid,
                    )
                    .with_for_update()
                )
                artifact = await database.scalar(
                    select(Artifact).where(
                        Artifact.id == uuid.UUID(artifact_id), Artifact.profile_id == profile_uuid
                    )
                )
                if artifact is None or operation is None:
                    return
                if operation.state != "queued":
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
                    artifact.processing_version = ARTIFACT_PROCESSOR_VERSION
                    operation.state = "succeeded"
                    operation.progress = {
                        "percent": 100,
                        "chunks": len(chunks),
                        "proposals": proposals,
                        "processor_version": ARTIFACT_PROCESSOR_VERSION,
                        "result": "proposals_created" if proposals else "no_proposals",
                    }
                    operation.updated_at = datetime.now(UTC)
                    await database.commit()
                except OperationalError:
                    await database.rollback()
                    raise
                except ArtifactError as error:
                    await _mark_artifact_failed(
                        database,
                        profile_uuid,
                        artifact.id,
                        operation.id,
                        error.code,
                        str(error),
                    )
                except KnowledgeError as error:
                    await _mark_artifact_failed(
                        database,
                        profile_uuid,
                        artifact.id,
                        operation.id,
                        error.code,
                        str(error),
                    )
                except Exception:
                    logger.exception(
                        "artifact_processing_failed",
                        extra={"artifact_id": str(artifact.id), "operation_id": str(operation.id)},
                    )
                    await _mark_artifact_failed(
                        database,
                        profile_uuid,
                        artifact.id,
                        operation.id,
                        "ARTIFACT_PROCESSING_FAILED",
                        "Artifact processing failed",
                    )
        finally:
            await services.close()

    asyncio.run(run())


@celery.task(name="career_assistant.recover_queued_artifact_operations")  # type: ignore[untyped-decorator]
def recover_queued_artifact_operations() -> int:
    async def run() -> int:
        services = Services.create(settings)
        dispatched = 0
        try:
            async with services.sessions() as database:
                profile_ids = (await database.scalars(select(Profile.id))).all()
                cutoff = datetime.now(UTC) - timedelta(minutes=1)
                for profile_id in profile_ids:
                    await set_profile_context(database, profile_id)
                    operations = (
                        await database.scalars(
                            select(Operation).where(
                                Operation.profile_id == profile_id,
                                Operation.kind == "artifact_import",
                                Operation.state == "queued",
                                Operation.created_at <= cutoff,
                            )
                        )
                    ).all()
                    for operation in operations:
                        process_artifact.delay(
                            str(operation.target_id), str(operation.id), str(profile_id)
                        )
                        dispatched += 1
        finally:
            await services.close()
        return dispatched

    return asyncio.run(run())
