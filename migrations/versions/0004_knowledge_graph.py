"""Add profile-scoped artifacts, evidence, graph history, and RLS."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_knowledge_graph"
down_revision = "0003_jobs_and_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "operation",
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_operation_profile_id", "operation", ["profile_id"])

    op.create_table(
        "artifact",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_content", sa.LargeBinary(), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("processing_state", sa.String(32), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("erased_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("profile_id", "content_hash"),
    )
    op.create_index("ix_artifact_profile_id", "artifact", ["profile_id"])
    op.create_index("ix_artifact_processing_state", "artifact", ["processing_state"])

    op.create_table(
        "kg_entity",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("profile_id", "id"),
    )
    op.create_index("ix_kg_entity_profile_id", "kg_entity", ["profile_id"])
    op.create_index("ix_kg_entity_entity_type", "kg_entity", ["entity_type"])
    op.create_index("ix_kg_entity_normalized_name", "kg_entity", ["normalized_name"])

    op.create_table(
        "kg_relation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("from_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("profile_id", "id"),
        sa.ForeignKeyConstraint(
            ["profile_id", "from_entity_id"], ["kg_entity.profile_id", "kg_entity.id"]
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "to_entity_id"], ["kg_entity.profile_id", "kg_entity.id"]
        ),
    )
    op.create_index("ix_kg_relation_profile_id", "kg_relation", ["profile_id"])
    op.create_index("ix_kg_relation_from", "kg_relation", ["from_entity_id", "relation_type"])
    op.create_index("ix_kg_relation_to", "kg_relation", ["to_entity_id", "relation_type"])

    op.create_table(
        "kg_assertion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("predicate", sa.String(64), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confidence_method", sa.String(64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1"),
        sa.UniqueConstraint("profile_id", "id"),
        sa.ForeignKeyConstraint(
            ["profile_id", "subject_entity_id"], ["kg_entity.profile_id", "kg_entity.id"]
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "relation_id"], ["kg_relation.profile_id", "kg_relation.id"]
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "supersedes_id"], ["kg_assertion.profile_id", "kg_assertion.id"]
        ),
    )
    op.create_index("ix_kg_assertion_profile_id", "kg_assertion", ["profile_id"])
    op.create_index("ix_kg_assertion_status", "kg_assertion", ["status"])

    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source_uri", sa.String(500), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_excerpt", sa.LargeBinary(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("locator", sa.String(300), nullable=False),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifact.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "raw_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_source_document.id"),
        ),
        sa.UniqueConstraint("profile_id", "id"),
    )
    op.create_index("ix_evidence_profile_id", "evidence", ["profile_id"])
    op.create_index("ix_evidence_content_hash", "evidence", ["content_hash"])
    op.create_index("ix_evidence_artifact_id", "evidence", ["artifact_id"])

    op.create_table(
        "assertion_evidence",
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("assertion_id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("support", sa.String(16), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1"),
        sa.Column("locator", sa.String(300), nullable=False, primary_key=True),
        sa.ForeignKeyConstraint(
            ["profile_id", "assertion_id"], ["kg_assertion.profile_id", "kg_assertion.id"]
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "evidence_id"], ["evidence.profile_id", "evidence.id"]
        ),
        sa.UniqueConstraint("profile_id", "assertion_id", "evidence_id", "locator"),
    )

    op.create_table(
        "graph_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("profile_id", "version"),
    )
    op.create_index("ix_graph_version_profile_id", "graph_version", ["profile_id"])

    op.create_table(
        "graph_change",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("graph_version", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("before", postgresql.JSONB()),
        sa.Column("after", postgresql.JSONB()),
        sa.ForeignKeyConstraint(
            ["profile_id", "graph_version"], ["graph_version.profile_id", "graph_version.version"]
        ),
    )
    op.create_index("ix_graph_change_profile_id", "graph_change", ["profile_id"])

    op.create_table(
        "knowledge_proposal",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposed_assertion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("kg_assertion.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("base_graph_version", sa.Integer(), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.String(2000)),
        sa.Column("replacement_assertion_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_knowledge_proposal_profile_id", "knowledge_proposal", ["profile_id"])
    op.create_index("ix_knowledge_proposal_state", "knowledge_proposal", ["state"])

    op.create_table(
        "outbox_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "audit_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
        ),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_event_profile_id", "audit_event", ["profile_id"])

    profile_tables = (
        "artifact",
        "kg_entity",
        "kg_relation",
        "kg_assertion",
        "evidence",
        "assertion_evidence",
        "graph_version",
        "graph_change",
        "knowledge_proposal",
        "feedback_event",
        "audit_event",
    )
    for table in profile_tables:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_profile_isolation ON {table} "
                "USING (profile_id = nullif(current_setting('career.profile_id', true), '')::uuid) "
                "WITH CHECK (profile_id = nullif(current_setting('career.profile_id', true), "
                "'')::uuid)"
            )
        )
    op.execute(sa.text("ALTER TABLE operation ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE operation FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY operation_profile_isolation ON operation "
            "USING (profile_id IS NULL OR profile_id = "
            "nullif(current_setting('career.profile_id', true), '')::uuid) "
            "WITH CHECK (profile_id IS NULL OR profile_id = "
            "nullif(current_setting('career.profile_id', true), '')::uuid)"
        )
    )


def downgrade() -> None:
    for table in (
        "audit_event",
        "outbox_event",
        "knowledge_proposal",
        "graph_change",
        "graph_version",
        "assertion_evidence",
        "evidence",
        "kg_assertion",
        "kg_relation",
        "kg_entity",
        "artifact",
    ):
        op.drop_table(table)
    op.drop_index("ix_operation_profile_id", table_name="operation")
    op.drop_column("operation", "profile_id")
