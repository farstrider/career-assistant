"""Add sources, durable job history, operations, and feedback."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_jobs_and_sources"
down_revision = "0002_local_authentication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("base_url", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acquisition_method", sa.String(32), nullable=False),
        sa.Column("policy_status", sa.String(32), nullable=False),
        sa.Column("policy_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("terms_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("robots_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("next_review_at", sa.DateTime(timezone=True)),
        sa.Column("policy_notes", sa.Text()),
        sa.Column("credential_custodian", sa.String(200)),
        sa.Column("requests_per_minute", sa.Integer(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("requests_per_minute >= 0"),
        sa.CheckConstraint("version > 0"),
    )
    op.create_table(
        "connector_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("cursor_before", sa.Text()),
        sa.Column("cursor_after", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unchanged_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_detail", sa.String(500)),
        sa.UniqueConstraint("source_id", "idempotency_key"),
    )
    op.create_index("ix_connector_run_source_id", "connector_run", ["source_id"])
    op.create_index("ix_connector_run_status", "connector_run", ["status"])
    op.create_table(
        "raw_source_document",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connector_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(500), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(200), nullable=False),
        sa.Column("content_encoding", sa.String(64)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.Column("headers", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("source_id", "content_hash"),
    )
    op.create_index("ix_raw_source_document_source_id", "raw_source_document", ["source_id"])
    op.create_table(
        "job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("company_name", sa.String(300), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("location_text", sa.String(500)),
        sa.Column("remote_policy", sa.String(32), nullable=False),
        sa.Column("employment_type", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posting_date", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_job_status", "job", ["status"])
    op.create_index("ix_job_discovered_at", "job", ["discovered_at"])
    op.create_table(
        "job_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("normalized_data", postgresql.JSONB(), nullable=False),
        sa.Column("field_provenance", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_hash", sa.String(64), nullable=False),
        sa.Column(
            "raw_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_source_document.id"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "version"),
        sa.UniqueConstraint("job_id", "normalized_hash"),
    )
    op.create_index("ix_job_version_job_id", "job_version", ["job_id"])
    op.create_table(
        "job_source_link",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("external_id", sa.String(500), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("source_id", "external_id"),
    )
    op.create_index("ix_job_source_link_last_seen_at", "job_source_link", ["last_seen_at"])
    op.create_table(
        "job_fingerprint",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("strength", sa.String(16), nullable=False),
        sa.UniqueConstraint("kind", "value"),
    )
    op.create_index("ix_job_fingerprint_job_id", "job_fingerprint", ["job_id"])
    op.create_table(
        "feedback_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.UniqueConstraint("profile_id", "idempotency_key"),
    )
    op.create_index("ix_feedback_event_profile_id", "feedback_event", ["profile_id"])
    op.create_index("ix_feedback_event_job_id", "feedback_event", ["job_id"])
    op.create_table(
        "operation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("progress", postgresql.JSONB(), nullable=False),
        sa.Column("problem_code", sa.String(64)),
        sa.Column("problem_detail", sa.String(500)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("requested_by_user_id", "idempotency_key"),
    )
    op.create_index("ix_operation_requested_by_user_id", "operation", ["requested_by_user_id"])
    op.create_index("ix_operation_state", "operation", ["state"])


def downgrade() -> None:
    for table in (
        "operation",
        "feedback_event",
        "job_fingerprint",
        "job_source_link",
        "job_version",
        "job",
        "raw_source_document",
        "connector_run",
        "source",
    ):
        op.drop_table(table)
