"""Datasources set up through table selection and required field semantics.

Tables and columns gain two statuses (no schema change: status is free text):
meta_table "pending" (found, not yet imported) and "excluded" (left out on purpose);
meta_column "pending" (found, waiting for a semantic). None of them is in the catalog.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("datasource") as batch:
        batch.add_column(
            sa.Column("require_semantics", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    # Nothing set up this way may surface half-described after a downgrade.
    op.execute("UPDATE meta_column SET status = 'missing' WHERE status = 'pending'")
    op.execute("UPDATE meta_table SET status = 'missing' WHERE status IN ('pending', 'excluded')")
    with op.batch_alter_table("datasource") as batch:
        batch.drop_column("require_semantics")
