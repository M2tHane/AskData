"""A short note on each evaluation run, so reviewers know what it was testing."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("eval_run") as batch:
        batch.add_column(sa.Column("note", sa.String(200), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("eval_run") as batch:
        batch.drop_column("note")
