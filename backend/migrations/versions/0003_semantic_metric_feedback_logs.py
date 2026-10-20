"""Semantic/metric governance, feedback, usage and a monthly partitioned query log.

The payload remains compatible with core.audit.QueryRun. PostgreSQL partition keys are
part of the primary key; downstream query_run references must carry created_at too.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def common(soft: bool = False) -> list[sa.Column]:
    columns = [
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
    ]
    if soft:
        columns.append(sa.Column("deleted_at", sa.DateTime(timezone=True)))
    return columns


def fk(name: str, table: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey(f"{table}.id"), nullable=nullable)


def scope_columns() -> list[sa.Column]:
    return [
        sa.Column("scope", sa.String(20), nullable=False),
        fk("owner_team_id", "team"),
        fk("owner_user_id", "user"),
        sa.Column("status", sa.String(20), nullable=False),
    ]


def scope_check(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "(scope = 'platform' AND owner_team_id IS NULL AND owner_user_id IS NULL "
        "AND status = 'active') OR (scope = 'team' AND owner_team_id IS NOT NULL "
        "AND status = 'team') OR (scope = 'personal' AND owner_team_id IS NOT NULL "
        "AND owner_user_id IS NOT NULL AND status IN ('private','pending'))",
        name=f"ck_{name}_scope",
    )


def upgrade_query_run(postgres: bool) -> None:
    if not postgres:
        op.create_index(
            "uq_query_run_id_created_at", "query_run", ["id", "created_at"], unique=True
        )
        for field in ("team_id", "user_id", "status"):
            op.create_index(
                f"ix_query_run_{field}_created_at",
                "query_run",
                [sa.text(f"json_extract(payload, '$.{field}')"), "created_at"],
            )
        return
    op.rename_table("query_run", "query_run_legacy")
    op.execute("ALTER INDEX ix_query_run_expires_at RENAME TO ix_query_run_legacy_expires_at")
    op.create_table(
        "query_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.BigInteger()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.Computed("(payload->>'user_id')::bigint", persisted=True),
            sa.ForeignKey("user.id"),
        ),
        sa.Column(
            "team_id",
            sa.BigInteger(),
            sa.Computed("(payload->>'team_id')::bigint", persisted=True),
            sa.ForeignKey("team.id"),
        ),
        sa.Column(
            "datasource_id",
            sa.BigInteger(),
            sa.Computed("(payload->>'datasource_id')::bigint", persisted=True),
            sa.ForeignKey("datasource.id"),
        ),
        sa.Column("status", sa.String(20), sa.Computed("payload->>'status'", persisted=True)),
        sa.PrimaryKeyConstraint("id", "created_at", name="pk_query_run_partitioned"),
        postgresql_partition_by="RANGE (created_at)",
    )
    # Three prior and twelve future months; the default partition preserves availability
    # beyond this window until the next reviewed Alembic partition maintenance migration.
    current = datetime.now(UTC)
    month_number = current.year * 12 + current.month - 1
    for delta in range(-3, 13):
        start = month_number + delta
        year, month = divmod(start, 12)
        next_year, next_month = divmod(start + 1, 12)
        name = f"query_run_{year:04d}_{month + 1:02d}"
        op.execute(
            f"CREATE TABLE {name} PARTITION OF query_run FOR VALUES FROM "
            f"('{year:04d}-{month + 1:02d}-01 00:00:00+00') TO "
            f"('{next_year:04d}-{next_month + 1:02d}-01 00:00:00+00')"
        )
    op.execute("CREATE TABLE query_run_default PARTITION OF query_run DEFAULT")
    op.execute(
        "INSERT INTO query_run (id,created_at,updated_at,created_by,expires_at,payload) "
        "SELECT id,created_at,updated_at,created_by,expires_at,payload::jsonb FROM query_run_legacy"
    )
    op.execute(
        "SELECT setval(pg_get_serial_sequence('query_run','id'), "
        "COALESCE((SELECT MAX(id) FROM query_run), 1), EXISTS(SELECT 1 FROM query_run))"
    )
    op.drop_table("query_run_legacy")
    op.create_index("ix_query_run_expires_at", "query_run", ["expires_at"])
    for field in ("team_id", "user_id", "status"):
        op.create_index(f"ix_query_run_{field}_created_at", "query_run", [field, "created_at"])
    op.create_index(
        "ix_query_run_datasource_created_at", "query_run", ["datasource_id", "created_at"]
    )
    op.create_index(
        "ix_query_run_blocked_created_at",
        "query_run",
        [sa.text("(payload->>'blocked_code')"), "created_at"],
    )


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    if postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    upgrade_query_run(postgres)
    op.create_table(
        "semantic_term",
        *common(True),
        *scope_columns(),
        fk("datasource_id", "datasource", False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        fk("target_table_id", "meta_table"),
        fk("target_column_id", "meta_column"),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enum_mapping", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        scope_check("semantic"),
        sa.CheckConstraint(
            "(target_type = 'table' AND target_table_id = target_id "
            "AND target_column_id IS NULL) OR (target_type IN ('column','enum') "
            "AND target_column_id = target_id AND target_table_id IS NULL)",
            name="ck_semantic_target",
        ),
    )
    op.create_table(
        "metric",
        *common(True),
        *scope_columns(),
        fk("datasource_id", "datasource", False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sql_template", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        scope_check("metric"),
    )
    for table in ("semantic_term", "metric"):
        op.create_index(
            f"ix_{table}_datasource_scope_team", table, ["datasource_id", "scope", "owner_team_id"]
        )
    op.create_table(
        "promotion_request",
        *common(),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("ref_id", sa.BigInteger(), nullable=False),
        fk("semantic_id", "semantic_term"),
        fk("metric_id", "metric"),
        fk("requested_by", "user", False),
        fk("team_id", "team", False),
        fk("reviewed_by", "user"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(kind = 'semantic' AND semantic_id = ref_id AND metric_id IS NULL) OR "
            "(kind = 'metric' AND metric_id = ref_id AND semantic_id IS NULL)",
            name="ck_promotion_ref",
        ),
    )
    op.create_index("ix_promotion_team_status", "promotion_request", ["team_id", "status"])
    op.create_index(
        "uq_promotion_pending",
        "promotion_request",
        ["kind", "ref_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "example_sql",
        *common(True),
        *scope_columns(),
        fk("datasource_id", "datasource", False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("review_comment", sa.Text(), nullable=False),
        sa.CheckConstraint("scope IN ('platform','team')", name="ck_example_scope"),
    )
    op.create_index(
        "ix_example_datasource_scope", "example_sql", ["datasource_id", "scope", "approved"]
    )
    op.create_table(
        "feedback",
        *common(),
        sa.Column("query_run_id", sa.BigInteger(), nullable=False),
        sa.Column("query_run_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["query_run_id", "query_run_created_at"],
            ["query_run.id", "query_run.created_at"],
            ondelete="CASCADE",
        ),
        fk("user_id", "user", False),
        fk("team_id", "team"),
        sa.Column("rating", sa.String(10), nullable=False),
        sa.Column("reason_code", sa.String(30)),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        fk("handled_by", "user"),
        sa.Column("handled_at", sa.DateTime(timezone=True)),
        sa.Column("converted_ref", sa.String(100)),
        fk("example_id", "example_sql"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rating IN ('up','down') AND (rating != 'down' OR reason_code IN "
            "('metric_wrong','table_wrong','column_wrong','empty_result','result_wrong','chart_wrong','other'))",
            name="ck_feedback_reason",
        ),
    )
    op.create_index("ix_feedback_expires_at", "feedback", ["expires_at"])
    op.create_index("ix_feedback_team_status", "feedback", ["team_id", "status"])
    op.create_table(
        "embedding_item",
        *common(),
        fk("datasource_id", "datasource", False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("ref_id", sa.BigInteger(), nullable=False),
        fk("table_id", "meta_table"),
        fk("column_id", "meta_column"),
        fk("semantic_id", "semantic_term"),
        fk("metric_id", "metric"),
        fk("example_id", "example_sql"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024) if postgres else sa.JSON()),
        sa.Column("model_id", sa.String(100)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("dirty", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
        fk("actor_id", "user", False),
        sa.UniqueConstraint("datasource_id", "kind", "ref_id", name="uq_embedding_ref"),
    )
    op.create_index("ix_embedding_datasource_kind", "embedding_item", ["datasource_id", "kind"])
    op.create_index("ix_embedding_item_dirty", "embedding_item", ["dirty"])
    if postgres:
        op.create_index(
            "ix_embedding_hnsw",
            "embedding_item",
            ["embedding"],
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        )
        op.create_index(
            "ix_embedding_text_trgm",
            "embedding_item",
            ["text"],
            postgresql_using="gin",
            postgresql_ops={"text": "gin_trgm_ops"},
        )
    op.create_table(
        "usage_daily",
        *common(),
        sa.Column("date", sa.Date(), nullable=False),
        fk("team_id", "team"),
        fk("user_id", "user", False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("date", "user_id", "model_id", name="uq_usage_daily"),
    )
    op.create_index("ix_usage_team_date", "usage_daily", ["team_id", "date"])
    op.create_table(
        "team_quota",
        *common(),
        fk("team_id", "team", False),
        sa.Column("monthly_token_limit", sa.BigInteger()),
        sa.Column("daily_query_limit", sa.Integer()),
        sa.Column("daily_date", sa.Date()),
        sa.Column("daily_calls", sa.Integer(), nullable=False),
        sa.UniqueConstraint("team_id"),
        sa.CheckConstraint(
            "monthly_token_limit IS NULL OR monthly_token_limit >= 0", name="ck_quota_monthly"
        ),
        sa.CheckConstraint(
            "daily_query_limit IS NULL OR daily_query_limit >= 0", name="ck_quota_daily"
        ),
    )
    op.create_table(
        "usage_reservation",
        *common(),
        sa.Column("reservation_id", sa.String(36), nullable=False, unique=True),
        fk("team_id", "team", False),
        fk("user_id", "user", False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_reservation_team_id", "usage_reservation", ["team_id"])
    op.create_index("ix_usage_reservation_expires_at", "usage_reservation", ["expires_at"])


def downgrade() -> None:
    for table in (
        "usage_reservation",
        "team_quota",
        "usage_daily",
        "embedding_item",
        "feedback",
        "example_sql",
        "promotion_request",
        "metric",
        "semantic_term",
    ):
        op.drop_table(table)
    postgres = op.get_bind().dialect.name == "postgresql"
    if postgres:
        # Preserve retained query history and restore the exact core storage shape.
        op.create_table(
            "query_run_unpartitioned",
            *common(),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
        )
        op.execute(
            "INSERT INTO query_run_unpartitioned "
            "(id,created_at,updated_at,created_by,expires_at,payload) "
            "SELECT id,created_at,updated_at,created_by,expires_at,payload FROM query_run"
        )
        op.drop_table("query_run")
        op.rename_table("query_run_unpartitioned", "query_run")
        op.execute(
            "SELECT setval(pg_get_serial_sequence('query_run','id'), "
            "COALESCE((SELECT MAX(id) FROM query_run),1), EXISTS(SELECT 1 FROM query_run))"
        )
        op.create_index("ix_query_run_expires_at", "query_run", ["expires_at"])
    else:
        for field in ("team_id", "user_id", "status"):
            op.drop_index(f"ix_query_run_{field}_created_at", table_name="query_run")
        op.drop_index("uq_query_run_id_created_at", table_name="query_run")
