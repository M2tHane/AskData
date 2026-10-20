"""Keep the chart configuration (never data) with each answer so history can be replayed."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("message") as batch:
        batch.add_column(sa.Column("chart_spec", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("message") as batch:
        batch.drop_column("chart_spec")
