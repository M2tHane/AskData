from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.modules.embedding.models import EmbeddingItem
from app.modules.embedding.schemas import IndexDocument, IndexReference


class EmbeddingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        async with self.session.begin_nested():
            yield

    async def mark_dirty(self, reference: IndexReference) -> None:
        insert = (
            pg_insert if self.session.get_bind().dialect.name == "postgresql" else sqlite_insert
        )
        statement = insert(EmbeddingItem).values(
            **reference.model_dump(),
            **{f"{reference.kind}_id": reference.ref_id},
            text="",
            dirty=True,
            version=0,
            revision=1,
            created_by=reference.actor_id,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["datasource_id", "kind", "ref_id"],
                set_={
                    "dirty": True,
                    "revision": EmbeddingItem.revision + 1,
                    "actor_id": reference.actor_id,
                    "updated_at": datetime.now(UTC),
                },
            )
        )

    async def pending(self, datasource_id: int) -> list[EmbeddingItem]:
        return list(
            await self.session.scalars(
                select(EmbeddingItem)
                .where(EmbeddingItem.datasource_id == datasource_id, EmbeddingItem.dirty.is_(True))
                .order_by(EmbeddingItem.id)
            )
        )

    async def save(
        self, document: IndexDocument, vector: list[float], model_id: str, revision: int
    ) -> bool:
        result = await self.session.execute(
            update(EmbeddingItem)
            .where(
                EmbeddingItem.datasource_id == document.datasource_id,
                EmbeddingItem.kind == document.kind,
                EmbeddingItem.ref_id == document.ref_id,
                EmbeddingItem.revision == revision,
            )
            .values(
                text=document.text,
                embedding=vector,
                model_id=model_id,
                version=document.version,
                dirty=False,
                indexed_at=datetime.now(UTC),
            )
            .returning(EmbeddingItem.id)
        )
        return result.scalar_one_or_none() is not None

    async def remove(self, reference: IndexReference, revision: int) -> None:
        await self.session.execute(
            delete(EmbeddingItem).where(
                EmbeddingItem.datasource_id == reference.datasource_id,
                EmbeddingItem.kind == reference.kind,
                EmbeddingItem.ref_id == reference.ref_id,
                EmbeddingItem.revision == revision,
            )
        )

    async def candidates(
        self,
        datasource_id: int,
        kinds: list[str],
        model_id: str,
        vector: list[float],
        query: str,
        limit: int,
        allowed: list[tuple[str, int]],
    ) -> list[EmbeddingItem]:
        if not allowed:
            return []
        statement = select(EmbeddingItem).where(
            tuple_(EmbeddingItem.kind, EmbeddingItem.ref_id).in_(allowed),
            EmbeddingItem.datasource_id == datasource_id,
            EmbeddingItem.kind.in_(kinds),
            EmbeddingItem.model_id == model_id,
            EmbeddingItem.embedding.is_not(None),
        )
        if self.session.get_bind().dialect.name == "postgresql":
            distance = EmbeddingItem.embedding.cosine_distance(vector)
            # Blend vector and trigram scores so keywords affect recall before LIMIT.
            keyword = func.similarity(EmbeddingItem.text, query)
            statement = statement.order_by(distance - keyword * 0.3, EmbeddingItem.id).limit(limit)
        else:
            statement = statement.order_by(EmbeddingItem.id).limit(limit)
        return list(await self.session.scalars(statement))

    async def nearest(
        self,
        datasource_id: int,
        model_id: str,
        vector: list[float],
        allowed: list[tuple[str, int]],
        limit: int,
    ) -> list[EmbeddingItem]:
        """Pure vector order: the caller fuses it with its own keyword ranking."""
        if not allowed:
            return []
        statement = select(EmbeddingItem).where(
            tuple_(EmbeddingItem.kind, EmbeddingItem.ref_id).in_(allowed),
            EmbeddingItem.datasource_id == datasource_id,
            EmbeddingItem.model_id == model_id,
            EmbeddingItem.embedding.is_not(None),
        )
        if self.session.get_bind().dialect.name == "postgresql":
            distance = EmbeddingItem.embedding.cosine_distance(vector)
            statement = statement.order_by(distance, EmbeddingItem.id)
        else:
            statement = statement.order_by(EmbeddingItem.id)
        return list(await self.session.scalars(statement.limit(limit)))

    async def snapshot(self, datasource_id: int) -> list[tuple[IndexReference, int]]:
        rows = await self.session.scalars(
            select(EmbeddingItem)
            .where(EmbeddingItem.datasource_id == datasource_id)
            .order_by(EmbeddingItem.id)
        )
        return [
            (
                IndexReference(
                    datasource_id=row.datasource_id,
                    kind=row.kind,
                    ref_id=row.ref_id,
                    actor_id=row.actor_id,
                ),
                row.revision,
            )
            for row in rows
        ]

    async def replace(
        self,
        datasource_id: int,
        documents: list[tuple[IndexDocument, list[float], str]],
        expected: list[tuple[IndexReference, int]],
    ) -> None:
        # Lock/check the generation before atomically replacing the old readable index.
        await self.session.scalars(
            select(EmbeddingItem)
            .where(EmbeddingItem.datasource_id == datasource_id)
            .with_for_update()
        )
        if await self.snapshot(datasource_id) != expected:
            raise AppError(ErrorCode.CONFLICT)
        await self.session.execute(
            delete(EmbeddingItem).where(EmbeddingItem.datasource_id == datasource_id)
        )
        for doc, vector, model_id in documents:
            actor_id = next(
                ref.actor_id
                for ref, _ in expected
                if ref.kind == doc.kind and ref.ref_id == doc.ref_id
            )
            values: dict[str, Any] = {
                "datasource_id": datasource_id,
                "kind": doc.kind,
                "ref_id": doc.ref_id,
                f"{doc.kind}_id": doc.ref_id,
                "actor_id": actor_id,
                "text": doc.text,
                "embedding": vector,
                "model_id": model_id,
                "version": doc.version,
                "revision": 1,
                "dirty": False,
                "indexed_at": datetime.now(UTC),
                "created_by": actor_id,
            }
            self.session.add(EmbeddingItem(**values))
        await self.session.flush()
