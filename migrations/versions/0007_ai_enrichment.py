"""Add versioned prompts, reasoning lineage, and validated job enrichment."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_ai_enrichment"
down_revision = "0006_artifact_processing_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_template",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("key", "version"),
    )
    op.create_index("ix_prompt_template_key", "prompt_template", ["key"])

    op.create_table(
        "reasoning_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "job_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_version.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "prompt_template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prompt_template.id"),
        ),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64)),
        sa.Column("request_id", sa.String(128)),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("validation_errors", postgresql.JSONB(), nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_reasoning_run_profile_id", "reasoning_run", ["profile_id"])
    op.create_index("ix_reasoning_run_job_version_id", "reasoning_run", ["job_version_id"])
    op.create_index("ix_reasoning_run_task", "reasoning_run", ["task"])
    op.create_index("ix_reasoning_run_state", "reasoning_run", ["state"])

    op.create_table(
        "job_enrichment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_version.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reasoning_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reasoning_run.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("job_version_id"),
    )
    op.create_index("ix_job_enrichment_job_version_id", "job_enrichment", ["job_version_id"])

    op.execute(sa.text("ALTER TABLE reasoning_run ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE reasoning_run FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY reasoning_run_profile_isolation ON reasoning_run "
            "USING (profile_id IS NULL OR profile_id = "
            "nullif(current_setting('career.profile_id', true), '')::uuid) "
            "WITH CHECK (profile_id IS NULL OR profile_id = "
            "nullif(current_setting('career.profile_id', true), '')::uuid)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS reasoning_run_profile_isolation ON reasoning_run"))
    op.execute(sa.text("ALTER TABLE reasoning_run NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE reasoning_run DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_job_enrichment_job_version_id", table_name="job_enrichment")
    op.drop_table("job_enrichment")
    op.drop_index("ix_reasoning_run_state", table_name="reasoning_run")
    op.drop_index("ix_reasoning_run_task", table_name="reasoning_run")
    op.drop_index("ix_reasoning_run_job_version_id", table_name="reasoning_run")
    op.drop_index("ix_reasoning_run_profile_id", table_name="reasoning_run")
    op.drop_table("reasoning_run")
    op.drop_index("ix_prompt_template_key", table_name="prompt_template")
    op.drop_table("prompt_template")
