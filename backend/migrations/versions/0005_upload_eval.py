"""File import history and regression evaluation records (D group)."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
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
        "upload_job",
        *common(),
        sa.Column("datasource_id", sa.BigInteger(), sa.ForeignKey("datasource.id"), nullable=False),
        sa.Column("owner_team_id", sa.BigInteger(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("file_key", sa.String(500), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("parse_config", sa.JSON(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("error", sa.String(100), nullable=True),
        sa.UniqueConstraint("datasource_id", "version"),
        sa.CheckConstraint("mode IN ('overwrite', 'append')", name="ck_upload_mode"),
        sa.CheckConstraint("version > 0 AND size > 0 AND row_count >= 0", name="ck_upload_counts"),
    )
    op.create_table(
        "eval_case",
        *common(),
        sa.Column("external_id", sa.String(100), nullable=False, unique=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("difficulty", sa.String(20), nullable=False),
        sa.Column("question", sa.String(), nullable=False),
        sa.Column("expected_sql", sa.String(), nullable=True),
        sa.Column("expected_result", sa.JSON(), nullable=True),
        sa.Column("expect_blocked", sa.Boolean(), nullable=False),
        sa.Column("expected_code", sa.String(100), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
    )
    op.create_table(
        "eval_run",
        *common(),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("prompt_versions", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("case_snapshot", sa.JSON(), nullable=False),
    )
    op.create_table(
        "eval_result",
        *common(),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("eval_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_id", sa.BigInteger(), sa.ForeignKey("eval_case.id"), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("actual_sql", sa.String(), nullable=True),
        sa.Column("actual_result_digest", sa.String(64), nullable=True),
        sa.Column("blocked_code", sa.String(100), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "case_id"),
    )


def downgrade() -> None:
    for table in ("eval_result", "eval_run", "eval_case", "upload_job"):
        op.drop_table(table)
