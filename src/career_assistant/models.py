from __future__ import annotations

import secrets
import time
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uuid7() -> uuid.UUID:
    """Generate an RFC 9562 UUIDv7 on the project's Python 3.13 runtime."""
    milliseconds = time.time_ns() // 1_000_000
    value = milliseconds << 80
    value |= 0x7 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    return uuid.UUID(int=value)


class Base(DeclarativeBase):
    pass


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    username: Mapped[str] = mapped_column(String(128), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Profile(Base):
    __tablename__ = "profile"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), unique=True
    )
    locale: Mapped[str] = mapped_column(String(32), default="en")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuthSession(Base):
    __tablename__ = "auth_session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_secret: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))


class Source(Base):
    __tablename__ = "source"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    acquisition_method: Mapped[str] = mapped_column(String(32))
    policy_status: Mapped[str] = mapped_column(String(32), default="pending_review")
    policy_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    robots_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    policy_notes: Mapped[str | None] = mapped_column(Text)
    credential_custodian: Mapped[str | None] = mapped_column(String(200))
    requests_per_minute: Mapped[int] = mapped_column(Integer, default=0)
    config: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConnectorRun(Base):
    __tablename__ = "connector_run"
    __table_args__ = (UniqueConstraint("source_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    cursor_before: Mapped[str | None] = mapped_column(Text)
    cursor_after: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(String(500))


class RawSourceDocument(Base):
    __tablename__ = "raw_source_document"
    __table_args__ = (UniqueConstraint("source_id", "content_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connector_run.id", ondelete="CASCADE")
    )
    external_id: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(String(200))
    content_encoding: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    body: Mapped[bytes] = mapped_column(LargeBinary)
    headers: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)


class Job(Base):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    canonical_url: Mapped[str] = mapped_column(Text)
    company_name: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(500))
    location_text: Mapped[str | None] = mapped_column(String(500))
    remote_policy: Mapped[str] = mapped_column(String(32), default="unspecified")
    employment_type: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    posting_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobVersion(Base):
    __tablename__ = "job_version"
    __table_args__ = (
        UniqueConstraint("job_id", "version"),
        UniqueConstraint("job_id", "normalized_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    normalized_data: Mapped[dict[str, object]] = mapped_column(JSONB)
    field_provenance: Mapped[dict[str, object]] = mapped_column(JSONB)
    normalized_hash: Mapped[str] = mapped_column(String(64))
    raw_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_source_document.id")
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class JobSourceLink(Base):
    __tablename__ = "job_source_link"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id", ondelete="CASCADE"), primary_key=True
    )
    external_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobFingerprint(Base):
    __tablename__ = "job_fingerprint"
    __table_args__ = (UniqueConstraint("kind", "value"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(String(64))
    strength: Mapped[str] = mapped_column(String(16))


class FeedbackEvent(Base):
    __tablename__ = "feedback_event"
    __table_args__ = (UniqueConstraint("profile_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profile.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, object]] = mapped_column("metadata", JSONB, default=dict)
    actor: Mapped[str] = mapped_column(String(32), default="member")
    idempotency_key: Mapped[str] = mapped_column(String(128))


class Operation(Base):
    __tablename__ = "operation"
    __table_args__ = (UniqueConstraint("requested_by_user_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), index=True)
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    progress: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    problem_code: Mapped[str | None] = mapped_column(String(64))
    problem_detail: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
