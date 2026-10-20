"""Datasource-level team scope with inherited table/column visibility; free employees."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006_g1"
branch_labels = None
depends_on = None


def _common() -> list[sa.Column]:
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
        batch.drop_constraint("ck_user_team", type_="check")
        batch.create_check_constraint("ck_user_team", "role <> 'leader' OR team_id IS NOT NULL")
    op.create_table(
        "datasource_team_scope",
        *_common(),
        sa.Column("datasource_id", sa.BigInteger(), sa.ForeignKey("datasource.id"), nullable=False),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("team.id"), nullable=False),
        sa.UniqueConstraint("datasource_id", "team_id"),
    )
    op.create_index("ix_datasource_team_scope_ds", "datasource_team_scope", ["datasource_id"])
    op.create_table(
        "scope_override",
        *_common(),
        sa.Column("object_type", sa.String(10), nullable=False),
        sa.Column("object_id", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("object_type", "object_id"),
    )
    _backfill(op.get_bind())


def _backfill(bind: sa.Connection) -> None:
    """Derive scope and overrides from existing grants so nothing changes for anyone."""
    meta = sa.MetaData()
    tables = sa.Table("meta_table", meta, autoload_with=bind)
    columns = sa.Table("meta_column", meta, autoload_with=bind)
    table_grants = sa.Table("team_table_grant", meta, autoload_with=bind)
    column_grants = sa.Table("team_column_grant", meta, autoload_with=bind)
    scope = sa.Table("datasource_team_scope", meta, autoload_with=bind)
    override = sa.Table("scope_override", meta, autoload_with=bind)

    table_teams: dict[int, set[int]] = {}
    for table_id, team_id in bind.execute(
        sa.select(table_grants.c.table_id, table_grants.c.team_id).where(
            table_grants.c.valid.is_(True)
        )
    ):
        table_teams.setdefault(table_id, set()).add(team_id)
    column_teams: dict[int, set[int]] = {}
    for column_id, team_id in bind.execute(
        sa.select(column_grants.c.column_id, column_grants.c.team_id).where(
            column_grants.c.valid.is_(True)
        )
    ):
        column_teams.setdefault(column_id, set()).add(team_id)
    by_datasource: dict[int, list[int]] = {}
    for table_id, datasource_id in bind.execute(sa.select(tables.c.id, tables.c.datasource_id)):
        by_datasource.setdefault(datasource_id, []).append(table_id)
    table_of: dict[int, int] = {
        column_id: table_id
        for column_id, table_id in bind.execute(sa.select(columns.c.id, columns.c.table_id))
    }
    for datasource_id, table_ids in by_datasource.items():
        teams = set().union(*(table_teams.get(t, set()) for t in table_ids))
        for team_id in sorted(teams):
            bind.execute(sa.insert(scope).values(datasource_id=datasource_id, team_id=team_id))
        for table_id in table_ids:
            if table_teams.get(table_id, set()) != teams:
                bind.execute(sa.insert(override).values(object_type="table", object_id=table_id))
    for column_id, table_id in table_of.items():
        if column_teams.get(column_id, set()) != table_teams.get(table_id, set()):
            bind.execute(sa.insert(override).values(object_type="column", object_id=column_id))


def downgrade() -> None:
    op.drop_table("scope_override")
    op.drop_index("ix_datasource_team_scope_ds", table_name="datasource_team_scope")
    op.drop_table("datasource_team_scope")
    with op.batch_alter_table("user") as batch:
        batch.drop_constraint("ck_user_team", type_="check")
        batch.create_check_constraint("ck_user_team", "role = 'admin' OR team_id IS NOT NULL")
