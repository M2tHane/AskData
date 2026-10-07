"""Conversation and message records only; query_run is owned by 0003/B group."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "conversation",
        *common(),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("team.id"), nullable=True),
        sa.Column("datasource_id", sa.BigInteger(), sa.ForeignKey("datasource.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_conversation_user_id", "conversation", ["user_id"])
    op.create_table(
        "message",
        *common(),
        sa.Column(
            "conversation_id", sa.BigInteger(), sa.ForeignKey("conversation.id"), nullable=False
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("query_run_id", sa.BigInteger(), nullable=True),
        sa.Column("query_run_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["query_run_id", "query_run_created_at"],
            ["query_run.id", "query_run.created_at"],
            ondelete="SET NULL",
        ),
        sa.Column("clarification_question", sa.Text(), nullable=True),
        sa.Column("clarification_choices", sa.Text(), nullable=True),
        sa.Column("clarification_answered", sa.Boolean(), nullable=False),
        sa.Column(
            "parent_message_id",
            sa.BigInteger(),
            sa.ForeignKey("message.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalized_sql", sa.Text(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_message_role"),
    )
    op.create_index("ix_message_conversation_id", "message", ["conversation_id"])
    op.create_index("ix_message_expires_at", "message", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_message_expires_at", table_name="message")
    op.drop_index("ix_message_conversation_id", table_name="message")
    op.drop_table("message")
    op.drop_index("ix_conversation_user_id", table_name="conversation")
    op.drop_table("conversation")
