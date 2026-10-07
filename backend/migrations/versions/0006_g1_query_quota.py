"""Durable, idempotent daily question admissions; model token reservations stay separate."""

import sqlalchemy as sa
from alembic import op

revision = "0006_g1"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_query",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("query_id", sa.String(128), nullable=False),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.UniqueConstraint("team_id", "query_id", name="uq_usage_query_team_query"),
    )
    op.create_index("ix_usage_query_team_date", "usage_query", ["team_id", "date"])
    # Preserve the old day's counter conservatively: legacy calls cannot be reconstructed as
    # logical questions. The next UTC day starts at zero; only new query IDs increment it.


def downgrade() -> None:
    op.drop_index("ix_usage_query_team_date", table_name="usage_query")
    op.drop_table("usage_query")
