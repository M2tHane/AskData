"""MCP connections: service accounts, keys, column allowlists, overrides, conversations."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _audit() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
    ]


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(sa.Column("kind", sa.String(10), nullable=False, server_default="person"))
    op.create_table(
        "mcp_client",
        *_audit(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("public_id", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column(
            "user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False, unique=True
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("daily_call_limit", sa.Integer(), nullable=False),
        sa.Column("default_rows", sa.Integer(), nullable=False),
        sa.Column("max_rows", sa.Integer(), nullable=False),
        sa.Column("ask_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("allow_run_sql", sa.Boolean(), nullable=False),
        sa.Column("daily_date", sa.Date(), nullable=True),
        sa.Column("daily_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "mcp_api_key",
        *_audit(),
        sa.Column("client_id", sa.BigInteger(), sa.ForeignKey("mcp_client.id"), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mcp_api_key_client_id", "mcp_api_key", ["client_id"])
    op.create_table(
        "mcp_column_grant",
        *_audit(),
        sa.Column("client_id", sa.BigInteger(), sa.ForeignKey("mcp_client.id"), nullable=False),
        sa.Column("column_id", sa.BigInteger(), sa.ForeignKey("meta_column.id"), nullable=False),
        sa.Column("mask_rule", sa.JSON(), nullable=True),
        sa.UniqueConstraint("client_id", "column_id"),
    )
    op.create_index("ix_mcp_column_grant_client_id", "mcp_column_grant", ["client_id"])
    op.create_table(
        "mcp_item_override",
        *_audit(),
        sa.Column("client_id", sa.BigInteger(), sa.ForeignKey("mcp_client.id"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("client_id", "kind", "item_id"),
    )
    op.create_index("ix_mcp_item_override_client_id", "mcp_item_override", ["client_id"])
    op.create_table(
        "mcp_conversation",
        *_audit(),
        sa.Column("public_id", sa.String(40), nullable=False, unique=True),
        sa.Column("client_id", sa.BigInteger(), sa.ForeignKey("mcp_client.id"), nullable=False),
        sa.Column(
            "conversation_id", sa.BigInteger(), sa.ForeignKey("conversation.id"), nullable=False
        ),
        sa.Column("datasource_id", sa.BigInteger(), sa.ForeignKey("datasource.id"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pending_message_id", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_mcp_conversation_client_id", "mcp_conversation", ["client_id"])


def downgrade() -> None:
    for table in ("mcp_conversation", "mcp_item_override", "mcp_column_grant", "mcp_api_key"):
        op.drop_table(table)
    op.drop_table("mcp_client")
    with op.batch_alter_table("user") as batch:
        batch.drop_column("kind")
