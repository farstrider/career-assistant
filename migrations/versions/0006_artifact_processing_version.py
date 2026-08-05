"""Track the artifact processor version used for derived knowledge."""

import sqlalchemy as sa
from alembic import op

revision = "0006_artifact_processing_version"
down_revision = "0005_profile_evolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifact",
        sa.Column("processing_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("artifact", "processing_version")
