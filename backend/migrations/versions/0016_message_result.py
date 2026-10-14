"""Keep the rows an answer showed, so reopening a conversation shows the same result.

Before this, history re-ran each answer's SQL on open. Rows are shown again only after the SQL
passes today's grants; masks are applied again on read.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_result",
        sa.Column(
            "message_id",
            sa.BigInteger(),
            sa.ForeignKey("message.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("preview_truncated", sa.Boolean(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("masked", sa.JSON(), nullable=False),
        sa.Column("queried_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reread", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_message_result_conversation_id", "message_result", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_message_result_conversation_id", table_name="message_result")
    op.drop_table("message_result")
