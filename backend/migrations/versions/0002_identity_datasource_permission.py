"""Identity, datasource metadata, authorization and model configuration."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def common(soft_delete: bool = True) -> list[sa.Column]:
    columns = [
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
    ]
    if soft_delete:
        columns.append(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    return columns


def fk(name: str, target: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey(target), nullable=nullable)


def text(name: str, size: int = 255, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.String(size), nullable=nullable)


def flag(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "team", *common(), text("name", 100), text("status", 20), sa.UniqueConstraint("name")
    )
    op.create_table(
        "user",
        *common(),
        text("username", 100),
        text("password_hash"),
        text("role", 20),
        fk("team_id", "team.id", True),
        text("status", 20),
        sa.UniqueConstraint("username"),
        sa.CheckConstraint("role IN ('admin','leader','member')", name="ck_user_role"),
        sa.CheckConstraint("role = 'admin' OR team_id IS NOT NULL", name="ck_user_team"),
    )
    op.create_table(
        "refresh_token",
        *common(False),
        fk("user_id", "user.id"),
        text("token_hash", 64),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        flag("revoked"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_refresh_token_expires_at", "refresh_token", ["expires_at"])
    op.create_table(
        "datasource",
        *common(),
        text("name", 100),
        text("type", 20),
        text("host"),
        sa.Column("port", sa.Integer(), nullable=False),
        text("database"),
        text("username"),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        text("schema_name"),
        text("dialect", 20),
        text("status", 20),
        flag("tls"),
        flag("read_only_account"),
        fk("owner_team_id", "team.id", True),
        sa.Column("upload_initialized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("upload_publications", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_table(
        "meta_table",
        *common(),
        fk("datasource_id", "datasource.id"),
        text("schema_name"),
        text("name"),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        text("status", 20),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("datasource_id", "schema_name", "name"),
    )
    op.create_index("ix_meta_table_datasource_id", "meta_table", ["datasource_id"])
    op.create_table(
        "meta_column",
        *common(),
        fk("table_id", "meta_table.id"),
        text("name"),
        text("data_type"),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        flag("is_pk"),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        text("status", 20),
        sa.UniqueConstraint("table_id", "name"),
    )
    op.create_index("ix_meta_column_table_id", "meta_column", ["table_id"])
    op.create_table(
        "meta_relation",
        *common(),
        fk("from_column_id", "meta_column.id"),
        fk("to_column_id", "meta_column.id"),
        text("source", 20),
    )
    op.create_table(
        "team_table_grant",
        *common(False),
        fk("team_id", "team.id"),
        fk("table_id", "meta_table.id"),
        flag("member_visible"),
        flag("valid"),
        sa.UniqueConstraint("team_id", "table_id"),
    )
    op.create_table(
        "team_column_grant",
        *common(False),
        fk("team_id", "team.id"),
        fk("column_id", "meta_column.id"),
        flag("member_visible"),
        flag("valid"),
        sa.Column("mask_rule", sa.JSON(), nullable=True),
        sa.Column("row_filter", sa.JSON(), nullable=False),
        sa.UniqueConstraint("team_id", "column_id"),
    )
    op.create_table(
        "model_config",
        *common(),
        text("type", 20),
        text("provider", 100),
        text("base_url", 2048, True),
        text("model_name"),
        sa.Column("api_key_enc", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        flag("enabled"),
        flag("is_default"),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
    )
    op.create_table(
        "team_model_pref",
        *common(False),
        fk("team_id", "team.id"),
        text("type", 20),
        fk("model_id", "model_config.id"),
        sa.UniqueConstraint("team_id", "type"),
    )


def downgrade() -> None:
    for name in (
        "team_model_pref",
        "model_config",
        "team_column_grant",
        "team_table_grant",
        "meta_relation",
        "meta_column",
        "meta_table",
        "datasource",
        "refresh_token",
        "user",
        "team",
    ):
        op.drop_table(name)
