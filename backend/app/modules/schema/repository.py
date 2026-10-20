from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.core.datasource import MetadataSnapshot
from app.core.db import AsyncSession, BaseRepository
from app.modules.schema.models import MetaColumn, MetaRelation, MetaTable
from app.modules.schema.schemas import ColumnRead, SyncResult, TableRead


class SchemaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tables = BaseRepository(session, MetaTable)
        self.columns = BaseRepository(session, MetaColumn)

    async def catalog(self, datasource_id: int) -> list[TableRead]:
        tables = list(
            await self.session.scalars(
                select(MetaTable)
                .where(
                    MetaTable.datasource_id == datasource_id,
                    MetaTable.status == "active",
                    MetaTable.deleted_at.is_(None),
                )
                .order_by(MetaTable.id)
            )
        )
        columns = list(
            await self.session.scalars(
                select(MetaColumn)
                .join(MetaTable)
                .where(
                    MetaTable.datasource_id == datasource_id,
                    MetaColumn.status == "active",
                    MetaColumn.deleted_at.is_(None),
                )
                .order_by(MetaColumn.ordinal)
            )
        )
        return [
            TableRead.model_validate(table).model_copy(
                update={
                    "columns": [
                        ColumnRead.model_validate(c) for c in columns if c.table_id == table.id
                    ]
                }
            )
            for table in tables
        ]

    async def setup_rows(self, datasource_id: int) -> tuple[list[MetaTable], list[MetaColumn]]:
        """Everything an administrator decides on: imported, pending and excluded tables with
        their active and pending columns (missing ones are gone from the database)."""
        tables = list(
            await self.session.scalars(
                select(MetaTable)
                .where(
                    MetaTable.datasource_id == datasource_id,
                    MetaTable.status.in_(("active", "pending", "excluded")),
                    MetaTable.deleted_at.is_(None),
                )
                .order_by(MetaTable.schema_name, MetaTable.name)
            )
        )
        columns = list(
            await self.session.scalars(
                select(MetaColumn)
                .join(MetaTable)
                .where(
                    MetaTable.datasource_id == datasource_id,
                    MetaColumn.status.in_(("active", "pending")),
                    MetaColumn.deleted_at.is_(None),
                )
                .order_by(MetaColumn.ordinal)
            )
        )
        return tables, columns

    async def lock(self, datasource_id: int) -> None:
        # Lock metadata as well as authorization writers' catalog reads.
        await self.session.scalars(
            select(MetaTable)
            .where(MetaTable.datasource_id == datasource_id)
            .order_by(MetaTable.id)
            .with_for_update()
        )

    async def synchronize(
        self,
        datasource_id: int,
        snapshot: MetadataSnapshot,
        actor_id: int,
        *,
        partial: bool = False,
        strict: bool = False,
    ) -> SyncResult:
        """Bring the catalog in line with the database.

        strict (a datasource set up with required semantics): a new table waits to be
        imported ("pending"), a new column without a comment waits for a semantic
        ("pending"), a table left out ("excluded") stays out, and one that went missing
        and came back is confirmed again. Nothing pending or excluded is in the catalog.
        """
        now = datetime.now(UTC)
        await self.lock(datasource_id)
        tables = list(
            await self.session.scalars(
                select(MetaTable).where(MetaTable.datasource_id == datasource_id)
            )
        )
        columns = list(
            await self.session.scalars(
                select(MetaColumn).join(MetaTable).where(MetaTable.datasource_id == datasource_id)
            )
        )
        by_table = {(t.schema_name, t.name): t for t in tables}
        by_column = {(c.table_id, c.name): c for c in columns}
        selected_keys = {(table.schema_name, table.name) for table in snapshot.tables}
        untouched_tables = (
            {table.id for table in tables if (table.schema_name, table.name) not in selected_keys}
            if partial
            else set()
        )
        seen_tables = set(untouched_tables)
        untouched_columns = {column.id for column in columns if column.table_id in untouched_tables}
        seen_columns = set(untouched_columns)
        added_tables: list[int] = []
        added_columns: list[int] = []
        pending_tables: list[int] = []
        pending_columns: list[int] = []
        column_names: dict[tuple[str, str], int] = {}
        for incoming in snapshot.tables:
            key = (incoming.schema_name, incoming.name)
            table = by_table.get(key)
            if table is None:
                table = await self.tables.create(
                    {
                        "datasource_id": datasource_id,
                        "schema_name": incoming.schema_name,
                        "name": incoming.name,
                        "comment": incoming.comment,
                        "synced_at": now,
                        "status": "pending" if strict else "active",
                    },
                    actor_id,
                )
                (pending_tables if strict else added_tables).append(table.id)
            else:
                if table.status in {"pending", "excluded"}:
                    status = table.status
                else:
                    status = "pending" if strict and table.status == "missing" else "active"
                table.comment, table.status, table.synced_at = incoming.comment, status, now
            seen_tables.add(table.id)
            for incoming_column in incoming.columns:
                column = by_column.get((table.id, incoming_column.name))
                values = incoming_column.model_dump()
                described = bool(
                    incoming_column.comment.strip() or (column and column.description.strip())
                )
                waiting = column is None or column.status in {"pending", "missing"}
                status = "pending" if strict and waiting and not described else "active"
                if column is None:
                    column = await self.columns.create(
                        {**values, "table_id": table.id, "status": status}, actor_id
                    )
                    if table.status == "active":
                        (added_columns if status == "active" else pending_columns).append(column.id)
                else:
                    await self.columns.update(column, {**values, "status": status})
                seen_columns.add(column.id)
                column_names[(f"{incoming.schema_name}.{incoming.name}", column.name)] = column.id
        # A table left out on purpose stays left out, present or not.
        removed_tables = [
            t.id for t in tables if t.id not in seen_tables and t.status != "excluded"
        ]
        removed_columns = [c.id for c in columns if c.id not in seen_columns]
        for table in tables:
            if table.id in removed_tables:
                table.status = "missing"
        for column in columns:
            if column.id in removed_columns:
                column.status = "missing"
        all_ids = sorted(({column.id for column in columns} | seen_columns) - untouched_columns)
        await self.session.execute(
            delete(MetaRelation).where(
                MetaRelation.source == "fk", MetaRelation.from_column_id.in_(all_ids)
            )
        )
        if removed_columns:
            await self.session.execute(
                delete(MetaRelation).where(
                    MetaRelation.from_column_id.in_(removed_columns)
                    | MetaRelation.to_column_id.in_(removed_columns)
                )
            )

        # Unqualified FK references are accepted only when exactly one table matches.
        def find(table: str, column: str) -> int | None:
            matches = [
                value
                for (name, col), value in column_names.items()
                if col == column and (name == table or name.rsplit(".", 1)[-1] == table)
            ]
            return matches[0] if len(matches) == 1 else None

        for relation in snapshot.relations:
            source, target = (
                find(relation.from_table, relation.from_column),
                find(relation.to_table, relation.to_column),
            )
            if source is not None and target is not None:
                self.session.add(
                    MetaRelation(
                        from_column_id=source, to_column_id=target, source="fk", created_by=actor_id
                    )
                )
        await self.session.flush()
        return SyncResult(
            datasource_id=datasource_id,
            removed_tables=removed_tables,
            removed_columns=removed_columns,
            table_count=len(seen_tables),
            added_tables=added_tables,
            added_columns=added_columns,
            pending_tables=pending_tables,
            pending_columns=pending_columns,
        )

    async def relations(self, column_ids: list[int]) -> list[MetaRelation]:
        return list(
            await self.session.scalars(
                select(MetaRelation).where(
                    MetaRelation.from_column_id.in_(column_ids),
                    MetaRelation.to_column_id.in_(column_ids),
                    MetaRelation.deleted_at.is_(None),
                )
            )
        )
