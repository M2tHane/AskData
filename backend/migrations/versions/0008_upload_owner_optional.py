"""Admin-created uploads have no owner team; visibility is set on the datasource."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("upload_job") as batch:
        batch.alter_column("owner_team_id", existing_type=sa.BigInteger(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("upload_job") as batch:
        batch.alter_column("owner_team_id", existing_type=sa.BigInteger(), nullable=False)
