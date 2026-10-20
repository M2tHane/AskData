"""Row-level permissions are removed: drop the unused row filter on column grants."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("team_column_grant") as batch:
        batch.drop_column("row_filter")


def downgrade() -> None:
    with op.batch_alter_table("team_column_grant") as batch:
        batch.add_column(sa.Column("row_filter", sa.JSON(), nullable=False, server_default="[]"))
