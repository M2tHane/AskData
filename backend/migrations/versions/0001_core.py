"""Shared operational records; future business migrations extend the query_run payload."""

import sqlalchemy as sa
from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    for name in ("audit_log", "query_run"):
        op.create_table(name, *common(), sa.Column("payload", sa.JSON(), nullable=False))
        op.create_index(f"ix_{name}_expires_at", name, ["expires_at"])
    op.create_table(
        "task_run",
        *common(),
        sa.Column("task_id", sa.String(36), nullable=False, unique=True),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(100), nullable=True),
    )
    op.create_index("ix_task_run_expires_at", "task_run", ["expires_at"])


def downgrade() -> None:
    for name in ("task_run", "query_run", "audit_log"):
        op.drop_index(f"ix_{name}_expires_at", table_name=name)
        op.drop_table(name)
