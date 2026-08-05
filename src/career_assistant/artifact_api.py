from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Header, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from career_assistant.artifacts import (
    ARTIFACT_PROCESSOR_VERSION,
    ArtifactError,
    artifact_cipher,
    content_hash,
    validate_artifact,
)
from career_assistant.auth import Current, Database, Mutation, problem
from career_assistant.models import Artifact, Operation

router = APIRouter()
logger = logging.getLogger(__name__)


class ArtifactResponse(BaseModel):
    id: uuid.UUID
    kind: str
    filename: str
    media_type: str
    size_bytes: int
    content_hash: str
    classification: str
    processing_state: str
    processing_version: int
    retention_until: datetime | None
    version: int
    created_at: datetime
    erased_at: datetime | None
    operation_url: str | None = None


def _artifact(item: Artifact, operation_id: uuid.UUID | None = None) -> ArtifactResponse:
    return ArtifactResponse(
        id=item.id,
        kind=item.kind,
        filename=item.filename,
        media_type=item.media_type,
        size_bytes=item.size_bytes,
        content_hash=item.content_hash,
        classification=item.classification,
        processing_state=item.processing_state,
        retention_until=item.retention_until,
        version=item.version,
        created_at=item.created_at,
        erased_at=item.erased_at,
        processing_version=item.processing_version,
        operation_url=f"/api/v1/operations/{operation_id}" if operation_id else None,
    )


@router.get("/artifacts", response_model=list[ArtifactResponse], tags=["artifacts"])
async def list_artifacts(current: Current, database: Database) -> list[ArtifactResponse]:
    items = (
        await database.scalars(
            select(Artifact)
            .where(Artifact.profile_id == current.profile.id)
            .order_by(Artifact.created_at.desc())
        )
    ).all()
    return [_artifact(item) for item in items]


@router.post(
    "/artifacts",
    response_model=ArtifactResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["artifacts"],
)
async def upload_artifact(
    request: Request,
    current: Mutation,
    database: Database,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> ArtifactResponse:
    existing_operation = await database.scalar(
        select(Operation).where(
            Operation.requested_by_user_id == current.user.id,
            Operation.idempotency_key == idempotency_key,
        )
    )
    if existing_operation:
        artifact = await database.get(Artifact, existing_operation.target_id)
        if artifact:
            return _artifact(artifact, existing_operation.id)
        raise problem(status.HTTP_404_NOT_FOUND, "ARTIFACT_NOT_FOUND", "Artifact not found")
    content = await file.read()
    try:
        media_type, kind = validate_artifact(
            file.filename or "artifact", file.content_type or "", content
        )
        encrypted = artifact_cipher(request.app.state.settings).encrypt(content)
    except (ArtifactError, AttributeError) as error:
        if isinstance(error, ArtifactError):
            raise problem(status.HTTP_422_UNPROCESSABLE_CONTENT, error.code, str(error)) from error
        raise problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "ARTIFACT_KEY_UNAVAILABLE",
            "Artifact encryption is unavailable",
        ) from error
    digest = content_hash(content)
    existing_artifact = await database.scalar(
        select(Artifact).where(
            Artifact.profile_id == current.profile.id,
            Artifact.content_hash == digest,
        )
    )
    if existing_artifact:
        needs_processing = (
            existing_artifact.processing_state not in {"completed", "awaiting_review"}
            or existing_artifact.processing_version < ARTIFACT_PROCESSOR_VERSION
        )
        operation = Operation(
            requested_by_user_id=current.user.id,
            profile_id=current.profile.id,
            kind="artifact_import",
            state="queued" if needs_processing else "succeeded",
            target_type="artifact",
            target_id=existing_artifact.id,
            progress={
                "deduplicated": True,
                "percent": 0 if needs_processing else 100,
                "processor_version": ARTIFACT_PROCESSOR_VERSION,
            },
            idempotency_key=idempotency_key,
        )
        database.add(operation)
        await database.commit()
        if needs_processing:
            _dispatch_artifact(operation, existing_artifact, current.profile.id)
        return _artifact(existing_artifact, operation.id)
    artifact = Artifact(
        profile_id=current.profile.id,
        kind=kind,
        filename=file.filename or "artifact",
        media_type=media_type,
        size_bytes=len(content),
        content_hash=digest,
        encrypted_content=encrypted,
        classification="private_career",
        processing_state="received",
    )
    database.add(artifact)
    await database.flush()
    operation = Operation(
        requested_by_user_id=current.user.id,
        profile_id=current.profile.id,
        kind="artifact_import",
        state="queued",
        target_type="artifact",
        target_id=artifact.id,
        progress={"percent": 0},
        idempotency_key=idempotency_key,
    )
    database.add(operation)
    await database.commit()
    _dispatch_artifact(operation, artifact, current.profile.id)
    return _artifact(artifact, operation.id)


def _dispatch_artifact(operation: Operation, artifact: Artifact, profile_id: uuid.UUID) -> None:
    from career_assistant.tasks import process_artifact

    try:
        process_artifact.delay(str(artifact.id), str(operation.id), str(profile_id))
    except Exception:
        logger.exception(
            "artifact_processing_enqueue_failed",
            extra={"artifact_id": str(artifact.id), "operation_id": str(operation.id)},
        )


@router.post(
    "/artifacts/{artifact_id}/reprocess",
    response_model=ArtifactResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["artifacts"],
)
async def reprocess_artifact(
    artifact_id: uuid.UUID,
    current: Mutation,
    database: Database,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> ArtifactResponse:
    existing_operation = await database.scalar(
        select(Operation).where(
            Operation.requested_by_user_id == current.user.id,
            Operation.idempotency_key == idempotency_key,
        )
    )
    if existing_operation:
        artifact = await database.get(Artifact, existing_operation.target_id)
        if artifact:
            return _artifact(artifact, existing_operation.id)
        raise problem(status.HTTP_404_NOT_FOUND, "ARTIFACT_NOT_FOUND", "Artifact not found")
    artifact = await database.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.profile_id == current.profile.id,
        )
    )
    if artifact is None:
        raise problem(status.HTTP_404_NOT_FOUND, "ARTIFACT_NOT_FOUND", "Artifact not found")
    operation = Operation(
        requested_by_user_id=current.user.id,
        profile_id=current.profile.id,
        kind="artifact_import",
        state="queued",
        target_type="artifact",
        target_id=artifact.id,
        progress={"percent": 0, "reprocess": True, "processor_version": ARTIFACT_PROCESSOR_VERSION},
        idempotency_key=idempotency_key,
    )
    database.add(operation)
    await database.commit()
    _dispatch_artifact(operation, artifact, current.profile.id)
    return _artifact(artifact, operation.id)


@router.get("/artifacts/{artifact_id}", response_model=ArtifactResponse, tags=["artifacts"])
async def get_artifact(
    artifact_id: uuid.UUID, current: Current, database: Database
) -> ArtifactResponse:
    item = await database.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id, Artifact.profile_id == current.profile.id
        )
    )
    if item is None:
        raise problem(status.HTTP_404_NOT_FOUND, "ARTIFACT_NOT_FOUND", "Artifact not found")
    operation = await database.scalar(
        select(Operation)
        .where(Operation.target_type == "artifact", Operation.target_id == artifact_id)
        .order_by(Operation.created_at.desc())
    )
    return _artifact(item, operation.id if operation else None)
