"""Allow normalized job content to recur in append-only version history."""

from alembic import op

revision = "0008_repeat_job_hashes"
down_revision = "0007_ai_enrichment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "job_version_job_id_normalized_hash_key",
        "job_version",
        type_="unique",
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "job_version_job_id_normalized_hash_key",
        "job_version",
        ["job_id", "normalized_hash"],
    )
