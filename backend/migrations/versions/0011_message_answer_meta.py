"""Keep each answer's structured explanation (interpretation, filters, definitions, reasoning)."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("message") as batch:
        batch.add_column(sa.Column("answer_meta", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("message") as batch:
        batch.drop_column("answer_meta")
