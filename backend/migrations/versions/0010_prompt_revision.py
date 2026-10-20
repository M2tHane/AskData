"""Administrator-published prompt text with history; seed files remain the fallback."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_revision",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("kind", "key", "revision"),
    )
    op.create_index("ix_prompt_revision_active", "prompt_revision", ["kind", "key", "is_active"])


def downgrade() -> None:
    op.drop_index("ix_prompt_revision_active", table_name="prompt_revision")
    op.drop_table("prompt_revision")
