"""Add proposal deferrals and learning observations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_profile_evolution"
down_revision = "0004_knowledge_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_proposal",
        sa.Column("defer_until", sa.DateTime(timezone=True)),
    )
    op.add_column("knowledge_proposal", sa.Column("decision_idempotency_key", sa.String(128)))
    op.create_table(
        "learning_observation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profile.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observation_key", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_evidence_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("suppressed_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("profile_id", "observation_key"),
        sa.UniqueConstraint("profile_id", "id", name="uq_learning_observation_profile_id_id"),
    )
    op.create_index("ix_learning_observation_profile_id", "learning_observation", ["profile_id"])
    op.create_index("ix_learning_observation_state", "learning_observation", ["state"])
    op.add_column(
        "knowledge_proposal",
        sa.Column(
            "observation_id",
            postgresql.UUID(as_uuid=True),
        ),
    )
    op.create_foreign_key(
        "fk_knowledge_proposal_observation_profile",
        "knowledge_proposal",
        "learning_observation",
        ["profile_id", "observation_id"],
        ["profile_id", "id"],
    )
    op.create_index(
        "ix_knowledge_proposal_observation_id", "knowledge_proposal", ["observation_id"]
    )
    op.execute(sa.text("ALTER TABLE learning_observation ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE learning_observation FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY learning_observation_profile_isolation ON learning_observation "
            "USING (profile_id = nullif(current_setting('career.profile_id', true), '')::uuid) "
            "WITH CHECK (profile_id = nullif(current_setting('career.profile_id', true), '')::uuid)"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS learning_observation_profile_isolation ON learning_observation"
        )
    )
    op.execute(sa.text("ALTER TABLE learning_observation NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE learning_observation DISABLE ROW LEVEL SECURITY"))
    op.drop_constraint(
        "fk_knowledge_proposal_observation_profile", "knowledge_proposal", type_="foreignkey"
    )
    op.drop_index("ix_knowledge_proposal_observation_id", table_name="knowledge_proposal")
    op.drop_column("knowledge_proposal", "observation_id")
    op.drop_constraint(
        "uq_learning_observation_profile_id_id", "learning_observation", type_="unique"
    )
    op.drop_index("ix_learning_observation_state", table_name="learning_observation")
    op.drop_index("ix_learning_observation_profile_id", table_name="learning_observation")
    op.drop_table("learning_observation")
    op.drop_column("knowledge_proposal", "defer_until")
    op.drop_column("knowledge_proposal", "decision_idempotency_key")
