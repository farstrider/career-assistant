from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select

from career_assistant.auth import Admin, AdminMutation, Current, Database, Mutation, problem
from career_assistant.ingestion import policy_allows
from career_assistant.models import (
    ConnectorRun,
    FeedbackEvent,
    Job,
    JobSourceLink,
    JobVersion,
    Operation,
    Source,
)

router = APIRouter()


class Page(BaseModel):
    items: list[dict[str, object]]
    next_cursor: str | None
    has_more: bool


class JobSummary(BaseModel):
    id: uuid.UUID
    title: str
    company_name: str
    location: str | None
    remote_policy: str
    employment_type: str | None
    status: str
    discovered_at: datetime
    posting_date: datetime | None
    sources: list[dict[str, str]]


class JobDetail(JobSummary):
    canonical_url: str
    version: int
    normalized: dict[str, object]
    provenance: dict[str, object]


class JobPage(BaseModel):
    items: list[JobSummary]
    next_cursor: str | None
    has_more: bool


class JobVersionResponse(BaseModel):
    version: int
    normalized: dict[str, object]
    provenance: dict[str, object]
    normalized_hash: str
    raw_document_id: uuid.UUID
    valid_from: datetime
    recorded_at: datetime


class FeedbackRequest(BaseModel):
    event_type: Literal[
        "ignored", "interested", "applied", "interview", "rejected", "offer", "accepted"
    ]
    occurred_at: datetime
    note: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @field_validator("metadata")
    @classmethod
    def bound_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        import json

        if len(json.dumps(value, ensure_ascii=False)) > 8192:
            raise ValueError("metadata exceeds 8192 bytes")
        return value


class FeedbackResponse(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    event_type: str
    occurred_at: datetime
    note: str | None
    metadata: dict[str, object]


class RunResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    cursor_before: str | None
    cursor_after: str | None
    started_at: datetime
    finished_at: datetime | None
    fetched_count: int
    new_count: int
    changed_count: int
    unchanged_count: int
    error_code: str | None
    error_detail: str | None


class SourceResponse(BaseModel):
    id: uuid.UUID
    key: str
    kind: str
    base_url: str | None
    enabled: bool
    acquisition_method: str
    policy_status: str
    policy_reviewed_at: datetime | None
    terms_reviewed_at: datetime | None
    robots_reviewed_at: datetime | None
    next_review_at: datetime | None
    policy_notes: str | None
    credential_custodian: str | None
    requests_per_minute: int
    safe_config: dict[str, object]
    version: int
    updated_at: datetime
    latest_run: RunResponse | None


class SourcePatch(BaseModel):
    enabled: bool | None = None
    acquisition_method: Literal["official_feed", "manual", "authorized_alert_email"] | None = None
    policy_status: Literal["pending_review", "approved", "rejected"] | None = None
    policy_reviewed_at: datetime | None = None
    terms_reviewed_at: datetime | None = None
    robots_reviewed_at: datetime | None = None
    next_review_at: datetime | None = None
    policy_notes: str | None = Field(default=None, max_length=4000)
    credential_custodian: str | None = Field(default=None, max_length=200)
    requests_per_minute: int | None = Field(default=None, ge=0, le=600)
    feed_url: str | None = None
    company_name: str | None = Field(default=None, max_length=300)
    parser: Literal["linkedin_jobs"] | None = None
    sender_allowlist: list[str] | None = Field(default=None, min_length=1, max_length=20)
    link_host_allowlist: list[str] | None = Field(default=None, min_length=1, max_length=20)

    @field_validator("sender_allowlist", "link_host_allowlist")
    @classmethod
    def nonempty_allowlist(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and any(not value.strip() for value in values):
            raise ValueError("allow-list entries must not be empty")
        return values


class OperationResponse(BaseModel):
    id: uuid.UUID
    kind: str
    state: str
    target_type: str
    target_id: uuid.UUID
    progress: dict[str, object]
    problem_code: str | None
    problem_detail: str | None
    created_at: datetime
    updated_at: datetime


def _cursor(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(base64.urlsafe_b64decode(value + "==").decode())
    except (ValueError, UnicodeDecodeError):
        raise problem(status.HTTP_400_BAD_REQUEST, "INVALID_CURSOR", "Cursor is invalid") from None


def _next_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


async def _job_sources(database: Database, job_id: uuid.UUID) -> list[dict[str, str]]:
    rows = (
        await database.execute(
            select(Source.key, JobSourceLink.url)
            .join(JobSourceLink, JobSourceLink.source_id == Source.id)
            .where(JobSourceLink.job_id == job_id)
            .order_by(Source.key)
        )
    ).all()
    return [{"key": key, "url": url} for key, url in rows]


async def _summary(database: Database, job: Job) -> JobSummary:
    return JobSummary(
        id=job.id,
        title=job.title,
        company_name=job.company_name,
        location=job.location_text,
        remote_policy=job.remote_policy,
        employment_type=job.employment_type,
        status=job.status,
        discovered_at=job.discovered_at,
        posting_date=job.posting_date,
        sources=await _job_sources(database, job.id),
    )


async def _job(database: Database, job_id: uuid.UUID) -> Job:
    job = await database.get(Job, job_id)
    if job is None:
        raise problem(status.HTTP_404_NOT_FOUND, "JOB_NOT_FOUND", "Job not found")
    return job


@router.get("/jobs", response_model=JobPage, tags=["jobs"])
async def list_jobs(
    _: Current,
    database: Database,
    q: str | None = Query(default=None, max_length=200),
    source: str | None = Query(default=None, max_length=128),
    discovered_after: datetime | None = None,
    status_filter: Annotated[str | None, Query(alias="status", max_length=32)] = None,
    location: str | None = Query(default=None, max_length=200),
    remote_policy: str | None = Query(default=None, max_length=32),
    sort: Literal["discovered", "company", "posting"] = "discovered",
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> JobPage:
    statement = select(Job)
    if q:
        term = f"%{q}%"
        statement = statement.where(or_(Job.title.ilike(term), Job.company_name.ilike(term)))
    if source:
        statement = statement.join(JobSourceLink).join(Source).where(Source.key == source)
    if discovered_after:
        statement = statement.where(Job.discovered_at >= discovered_after)
    if status_filter:
        statement = statement.where(Job.status == status_filter)
    if location:
        statement = statement.where(Job.location_text.ilike(f"%{location}%"))
    if remote_policy:
        statement = statement.where(Job.remote_policy == remote_policy)
    ordering = {
        "discovered": (Job.discovered_at.desc(), Job.id.desc()),
        "company": (Job.company_name, Job.title, Job.id),
        "posting": (Job.posting_date.desc().nullslast(), Job.id.desc()),
    }[sort]
    offset = _cursor(cursor)
    jobs = (
        await database.scalars(statement.order_by(*ordering).offset(offset).limit(limit + 1))
    ).all()
    has_more = len(jobs) > limit
    visible = jobs[:limit]
    return JobPage(
        items=[await _summary(database, job) for job in visible],
        next_cursor=_next_cursor(offset + limit) if has_more else None,
        has_more=has_more,
    )


@router.get("/jobs/{job_id}", response_model=JobDetail, tags=["jobs"])
async def get_job(job_id: uuid.UUID, _: Current, database: Database) -> JobDetail:
    job = await _job(database, job_id)
    version = await database.scalar(
        select(JobVersion).where(JobVersion.job_id == job.id).order_by(JobVersion.version.desc())
    )
    assert version is not None
    summary = await _summary(database, job)
    return JobDetail(
        **summary.model_dump(),
        canonical_url=job.canonical_url,
        version=version.version,
        normalized=version.normalized_data,
        provenance=version.field_provenance,
    )


@router.get("/jobs/{job_id}/versions", response_model=list[JobVersionResponse], tags=["jobs"])
async def list_job_versions(
    job_id: uuid.UUID, _: Current, database: Database
) -> list[JobVersionResponse]:
    await _job(database, job_id)
    versions = (
        await database.scalars(
            select(JobVersion)
            .where(JobVersion.job_id == job_id)
            .order_by(JobVersion.version.desc())
        )
    ).all()
    return [
        JobVersionResponse(
            version=item.version,
            normalized=item.normalized_data,
            provenance=item.field_provenance,
            normalized_hash=item.normalized_hash,
            raw_document_id=item.raw_document_id,
            valid_from=item.valid_from,
            recorded_at=item.recorded_at,
        )
        for item in versions
    ]


def _feedback(item: FeedbackEvent) -> FeedbackResponse:
    return FeedbackResponse(
        id=item.id,
        job_id=item.job_id,
        event_type=item.event_type,
        occurred_at=item.occurred_at,
        note=item.note,
        metadata=item.metadata_,
    )


@router.get("/jobs/{job_id}/feedback", response_model=list[FeedbackResponse], tags=["jobs"])
async def list_feedback(
    job_id: uuid.UUID, current: Current, database: Database
) -> list[FeedbackResponse]:
    await _job(database, job_id)
    items = (
        await database.scalars(
            select(FeedbackEvent)
            .where(FeedbackEvent.job_id == job_id, FeedbackEvent.profile_id == current.profile.id)
            .order_by(FeedbackEvent.occurred_at.desc())
        )
    ).all()
    return [_feedback(item) for item in items]


@router.post(
    "/jobs/{job_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["jobs"],
)
async def add_feedback(
    job_id: uuid.UUID,
    values: FeedbackRequest,
    current: Mutation,
    database: Database,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> FeedbackResponse:
    await _job(database, job_id)
    existing = await database.scalar(
        select(FeedbackEvent).where(
            FeedbackEvent.profile_id == current.profile.id,
            FeedbackEvent.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return _feedback(existing)
    item = FeedbackEvent(
        profile_id=current.profile.id,
        job_id=job_id,
        event_type=values.event_type,
        occurred_at=values.occurred_at.astimezone(UTC),
        note=values.note,
        metadata_=values.metadata,
        actor="member",
        idempotency_key=idempotency_key,
    )
    database.add(item)
    await database.commit()
    return _feedback(item)


def _run(item: ConnectorRun) -> RunResponse:
    return RunResponse.model_validate(item, from_attributes=True)


async def _source(database: Database, source_id: uuid.UUID) -> Source:
    item = await database.get(Source, source_id)
    if item is None:
        raise problem(status.HTTP_404_NOT_FOUND, "SOURCE_NOT_FOUND", "Source not found")
    return item


async def _source_response(database: Database, source: Source) -> SourceResponse:
    latest = await database.scalar(
        select(ConnectorRun)
        .where(ConnectorRun.source_id == source.id)
        .order_by(ConnectorRun.started_at.desc())
    )
    return SourceResponse(
        id=source.id,
        key=source.key,
        kind=source.kind,
        base_url=source.base_url,
        enabled=source.enabled,
        acquisition_method=source.acquisition_method,
        policy_status=source.policy_status,
        policy_reviewed_at=source.policy_reviewed_at,
        terms_reviewed_at=source.terms_reviewed_at,
        robots_reviewed_at=source.robots_reviewed_at,
        next_review_at=source.next_review_at,
        policy_notes=source.policy_notes,
        credential_custodian=source.credential_custodian,
        requests_per_minute=source.requests_per_minute,
        safe_config={
            key: value
            for key, value in source.config.items()
            if key
            in {
                "feed_url",
                "company_name",
                "parser",
                "sender_allowlist",
                "link_host_allowlist",
            }
        },
        version=source.version,
        updated_at=source.updated_at,
        latest_run=_run(latest) if latest else None,
    )


@router.get("/sources", response_model=list[SourceResponse], tags=["sources"])
async def list_sources(_: Admin, database: Database) -> list[SourceResponse]:
    sources = (await database.scalars(select(Source).order_by(Source.key))).all()
    return [await _source_response(database, source) for source in sources]


@router.get("/sources/runs/{run_id}", response_model=RunResponse, tags=["sources"])
async def get_source_run(run_id: uuid.UUID, _: Admin, database: Database) -> RunResponse:
    item = await database.get(ConnectorRun, run_id)
    if item is None:
        raise problem(status.HTTP_404_NOT_FOUND, "RUN_NOT_FOUND", "Source run not found")
    return _run(item)


@router.get("/sources/{source_id}", response_model=SourceResponse, tags=["sources"])
async def get_source(source_id: uuid.UUID, _: Admin, database: Database) -> SourceResponse:
    return await _source_response(database, await _source(database, source_id))


@router.patch("/sources/{source_id}", response_model=SourceResponse, tags=["sources"])
async def update_source(
    source_id: uuid.UUID,
    values: SourcePatch,
    _: AdminMutation,
    database: Database,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> SourceResponse:
    source = await _source(database, source_id)
    try:
        expected = int(if_match.strip('"'))
    except ValueError:
        raise problem(
            status.HTTP_400_BAD_REQUEST, "INVALID_VERSION", "If-Match is invalid"
        ) from None
    if source.version != expected:
        raise problem(
            status.HTTP_412_PRECONDITION_FAILED,
            "SOURCE_VERSION_MISMATCH",
            "Source changed; reload before saving",
        )
    for key in ("enabled", "acquisition_method", "policy_status", "requests_per_minute"):
        value = getattr(values, key)
        if value is not None:
            setattr(source, key, value)
    for key in (
        "policy_reviewed_at",
        "terms_reviewed_at",
        "robots_reviewed_at",
        "next_review_at",
        "policy_notes",
        "credential_custodian",
    ):
        if key in values.model_fields_set:
            setattr(source, key, getattr(values, key))
    config = dict(source.config)
    if values.feed_url is not None:
        config["feed_url"] = values.feed_url
    if values.company_name is not None:
        config["company_name"] = values.company_name.strip()
    if values.parser is not None:
        config["parser"] = values.parser
    if values.sender_allowlist is not None:
        config["sender_allowlist"] = [value.strip().casefold() for value in values.sender_allowlist]
    if values.link_host_allowlist is not None:
        config["link_host_allowlist"] = [
            value.strip().casefold() for value in values.link_host_allowlist
        ]
    source.config = config
    source.version += 1
    if source.enabled:
        try:
            policy_allows(source)
        except Exception as error:
            raise problem(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "SOURCE_POLICY_BLOCKED",
                str(error),
            ) from error
    await database.commit()
    return await _source_response(database, source)


@router.post(
    "/sources/{source_id}/runs",
    response_model=OperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["sources"],
)
async def request_source_run(
    source_id: uuid.UUID,
    current: AdminMutation,
    database: Database,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> OperationResponse:
    source = await _source(database, source_id)
    try:
        policy_allows(source)
    except Exception as error:
        raise problem(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "SOURCE_POLICY_BLOCKED", str(error)
        ) from error
    operation = await database.scalar(
        select(Operation).where(
            Operation.requested_by_user_id == current.user.id,
            Operation.idempotency_key == idempotency_key,
        )
    )
    created = operation is None
    if created:
        run = ConnectorRun(
            source_id=source.id,
            status="queued",
            idempotency_key=idempotency_key,
            started_at=datetime.now(UTC),
        )
        database.add(run)
        await database.flush()
        operation = Operation(
            requested_by_user_id=current.user.id,
            kind="source_run",
            state="queued",
            target_type="connector_run",
            target_id=run.id,
            progress={},
            idempotency_key=idempotency_key,
        )
        database.add(operation)
        await database.commit()
    if created:
        from career_assistant.tasks import scan_source

        assert operation is not None
        scan_source.delay(str(source.id), str(operation.target_id), str(operation.id))
    assert operation is not None
    return OperationResponse.model_validate(operation, from_attributes=True)


@router.get("/sources/{source_id}/runs", response_model=list[RunResponse], tags=["sources"])
async def list_source_runs(source_id: uuid.UUID, _: Admin, database: Database) -> list[RunResponse]:
    await _source(database, source_id)
    items = (
        await database.scalars(
            select(ConnectorRun)
            .where(ConnectorRun.source_id == source_id)
            .order_by(ConnectorRun.started_at.desc())
            .limit(100)
        )
    ).all()
    return [_run(item) for item in items]


@router.get("/operations/{operation_id}", response_model=OperationResponse, tags=["operations"])
async def get_operation(
    operation_id: uuid.UUID, current: Admin, database: Database, response: Response
) -> OperationResponse:
    item = await database.get(Operation, operation_id)
    if item is None or item.requested_by_user_id != current.user.id:
        raise problem(status.HTTP_404_NOT_FOUND, "OPERATION_NOT_FOUND", "Operation not found")
    if item.state in {"queued", "running"}:
        response.headers["Retry-After"] = "2"
    return OperationResponse.model_validate(item, from_attributes=True)
