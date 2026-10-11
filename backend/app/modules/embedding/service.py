import math
from typing import TYPE_CHECKING, Protocol

from app.core.audit import AuditService
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.llm import CallContext, EmbeddingClient, ModelConfig, ModelRegistry
from app.core.permission import PermissionService
from app.core.scope import ScopedQuery
from app.core.tasks import TaskContext, TaskManager, TaskRecord
from app.modules.embedding.repository import EmbeddingRepository
from app.modules.embedding.schemas import IndexDocument, IndexReference
from app.modules.embedding.schemas import SearchInput as SearchInput

if TYPE_CHECKING:
    from app.modules.feedback.service import FeedbackService
    from app.modules.metric.service import MetricService
    from app.modules.org.service import OrgService
    from app.modules.schema.service import SchemaService
    from app.modules.semantic.service import SemanticService


class IndexSource(Protocol):
    """Adapters call schema/semantic/metric/feedback services, including live authorization."""

    async def document(
        self, reference: IndexReference, actor: CurrentUser
    ) -> IndexDocument | None: ...
    async def catalog(self, datasource_id: int) -> list[IndexReference]: ...


class ActorResolver(Protocol):
    async def get_actor(self, identifier: int) -> CurrentUser: ...


class _PinnedModel:
    def __init__(self, model: ModelConfig) -> None:
        self.model = model.model_copy(update={"fallback_id": None})

    async def configurations(self) -> list[ModelConfig]:
        return [self.model]

    async def preference(self, team_id: int | None, kind: str) -> str | None:
        return self.model.id

    async def default(self, kind: str) -> str | None:
        return self.model.id


# Texts per embedding request when rebuilding an index.
EMBED_BATCH = 10


class EmbeddingService:
    UPDATE_TASK = "embedding.update"
    REBUILD_TASK = "embedding.rebuild"

    def __init__(
        self,
        repository: EmbeddingRepository,
        permissions: PermissionService,
        client: EmbeddingClient,
        source: IndexSource,
        actors: ActorResolver,
        audit: AuditService,
        dimensions: int = 1024,
    ) -> None:
        self.repository, self.permissions, self.client = repository, permissions, client
        self.source, self.actors, self.audit, self.dimensions = source, actors, audit, dimensions

    async def mark_dirty(
        self, kind: str, ref_id: int, datasource_id: int, *, actor: CurrentUser
    ) -> None:
        if not actor.active:
            raise PermissionDenied()
        # This durable outbox shares the source transaction. Worker picks it up after commit.
        await self.repository.mark_dirty(
            IndexReference.model_validate(
                {
                    "kind": kind,
                    "ref_id": ref_id,
                    "datasource_id": datasource_id,
                    "actor_id": actor.id,
                }
            )
        )

    async def enqueue(
        self, datasource_id: int, manager: TaskManager, *, actor: CurrentUser, rebuild: bool = False
    ) -> TaskRecord:
        if not actor.active or actor.role != Role.ADMIN:
            raise PermissionDenied()
        await self.permissions.visible_scope(actor, datasource_id)
        task = await manager.enqueue(
            self.REBUILD_TASK if rebuild else self.UPDATE_TASK,
            {"datasource_id": datasource_id, "actor_id": actor.id},
        )
        await self.audit.record(
            actor, "embedding.rebuild" if rebuild else "embedding.update", ("task_run", task.id)
        )
        return task

    async def _document(
        self, reference: IndexReference, actor: CurrentUser
    ) -> IndexDocument | None:
        scope = await self.permissions.visible_scope(actor, reference.datasource_id)
        document = await self.source.document(reference, actor)
        if document is None:
            return None
        if (
            document.datasource_id != reference.datasource_id
            or document.kind != reference.kind
            or document.ref_id != reference.ref_id
            or not ScopedQuery.visible_to([document], actor)
        ):
            raise PermissionDenied()
        if document.kind == "example" and not document.approved:
            return None
        # Descriptions may mention any referenced column; never embed a hidden object.
        if not document.references:
            raise PermissionDenied()
        for table, columns in document.references.items():
            visible = scope.tables.get(table)
            if visible is None or not columns <= visible.columns:
                raise PermissionDenied()
        return document

    async def _embed(self, text: str, actor: CurrentUser) -> tuple[list[float], str]:
        vectors, model_id = await self._embed_many([text], actor)
        return vectors[0], model_id

    async def _embed_many(
        self, texts: list[str], actor: CurrentUser
    ) -> tuple[list[list[float]], str]:
        model = await self.client.registry.select(actor.team_id, "embedding")
        # Pin the invocation to one ID: EmbeddingClient returns vectors without model metadata.
        pinned = EmbeddingClient(
            ModelRegistry(_PinnedModel(model)), self.client.recorder, self.client.transport
        )
        vectors = await pinned.embed(
            texts,
            CallContext(
                user_id=actor.id, team_id=actor.team_id, prompt_version="embedding_index:v1"
            ),
        )
        if len(vectors) != len(texts) or not all(vectors):
            raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)
        for vector in vectors:
            if len(vector) != self.dimensions or any(not math.isfinite(v) for v in vector):
                raise AppError(ErrorCode.CONFLICT)
        return vectors, model.id

    async def nearest(
        self,
        datasource_id: int,
        query: str,
        keys: list[tuple[str, int]],
        actor: CurrentUser,
        limit: int = 30,
    ) -> list[tuple[str, int]]:
        """Indexed documents closest to the query, among keys the caller already authorized.

        The chat retrieval builds its candidates from the user's visible scope, so this skips
        the per-document checks of search(); it never returns a key outside the given ones.
        """
        if not keys:
            return []
        vector, model_id = await self._embed(query, actor)
        rows = await self.repository.nearest(datasource_id, model_id, vector, keys, limit)
        allowed = set(keys)
        return [(row.kind, row.ref_id) for row in rows if (row.kind, row.ref_id) in allowed]

    async def update_task(self, context: TaskContext) -> None:
        datasource_id = int(context.task.payload["datasource_id"])
        rows = await self.repository.pending(datasource_id)
        for index, row in enumerate(rows):
            ref = IndexReference(
                kind=row.kind,
                ref_id=row.ref_id,
                datasource_id=row.datasource_id,
                actor_id=row.actor_id,
            )
            actor = await self.actors.get_actor(row.actor_id)
            try:
                document = await self._document(ref, actor)
            except AppError as error:
                if error.code not in {ErrorCode.FORBIDDEN, ErrorCode.NOT_FOUND}:
                    raise
                document = None
            async with self.repository.atomic():
                if document is None:
                    await self.repository.remove(ref, row.revision)
                else:
                    vector, model_id = await self._embed(document.text, actor)
                    await self.repository.save(document, vector, model_id, row.revision)
            await context.progress(int((index + 1) / len(rows) * 100))

    async def rebuild_task(self, context: TaskContext) -> None:
        datasource_id = int(context.task.payload["datasource_id"])
        # Catalog comes from live source services and covers entries missing from the old index.
        for reference in await self.source.catalog(datasource_id):
            await self.repository.mark_dirty(reference)
        expected = await self.repository.snapshot(datasource_id)
        staged: list[tuple[IndexDocument, list[float], str]] = []
        batch: list[IndexDocument] = []
        batch_actor: CurrentUser | None = None

        async def flush() -> None:
            if batch and batch_actor is not None:
                vectors, model_id = await self._embed_many([d.text for d in batch], batch_actor)
                staged.extend((d, v, model_id) for d, v in zip(batch, vectors, strict=True))
                batch.clear()

        for index, (reference, _) in enumerate(expected):
            actor = await self.actors.get_actor(reference.actor_id)
            document = await self._document(reference, actor)
            if document is not None:
                # Providers cap inputs per request (DashScope: 10); one call per batch.
                if batch_actor is not None and (
                    actor.id != batch_actor.id or len(batch) >= EMBED_BATCH
                ):
                    await flush()
                batch.append(document)
                batch_actor = actor
            await context.progress(int((index + 1) / len(expected) * 90))
        await flush()
        async with self.repository.atomic():
            await self.repository.replace(datasource_id, staged, expected)
        await context.progress(100)

    async def search(self, data: SearchInput, actor: CurrentUser) -> list[IndexDocument]:
        await self.permissions.visible_scope(actor, data.datasource_id)
        allowed: list[tuple[str, int]] = []
        for reference in await self.source.catalog(data.datasource_id):
            if reference.kind not in data.kinds:
                continue
            try:
                document = await self._document(reference, actor)
            except AppError as error:
                if error.code in {
                    ErrorCode.FORBIDDEN,
                    ErrorCode.NOT_FOUND,
                    ErrorCode.SQL_OUT_OF_SCOPE,
                }:
                    continue
                raise
            if document is not None:
                allowed.append((reference.kind, reference.ref_id))
        if not allowed:
            await self.audit.record(
                actor, "embedding.search", ("datasource", str(data.datasource_id))
            )
            return []
        vector, model_id = await self._embed(data.query, actor)
        rows = await self.repository.candidates(
            data.datasource_id,
            list(data.kinds),
            model_id,
            vector,
            data.query,
            min(data.limit * 5, 500),
            allowed,
        )
        documents = []
        for row in rows:
            try:
                document = await self._document(
                    IndexReference(
                        kind=row.kind,
                        ref_id=row.ref_id,
                        datasource_id=row.datasource_id,
                        actor_id=row.actor_id,
                    ),
                    actor,
                )
            except AppError as error:
                if error.code in {
                    ErrorCode.FORBIDDEN,
                    ErrorCode.NOT_FOUND,
                    ErrorCode.SQL_OUT_OF_SCOPE,
                }:
                    continue
                raise
            if document is not None:
                documents.append(document)
            if len(documents) >= data.limit:
                break
        await self.audit.record(actor, "embedding.search", ("datasource", str(data.datasource_id)))
        return documents


class IndexOutbox:
    """Lightweight source-side publisher. No model/network work runs in save transactions."""

    def __init__(self, repository: EmbeddingRepository) -> None:
        self.repository = repository

    async def mark_dirty(
        self, kind: str, ref_id: int, datasource_id: int, *, actor: CurrentUser
    ) -> None:
        if not actor.active:
            raise PermissionDenied()
        await self.repository.mark_dirty(
            IndexReference.model_validate(
                {
                    "kind": kind,
                    "ref_id": ref_id,
                    "datasource_id": datasource_id,
                    "actor_id": actor.id,
                }
            )
        )


def build_outbox(session: "AsyncSession") -> IndexOutbox:
    return IndexOutbox(EmbeddingRepository(session))


class ServiceIndexSource:
    """Concrete source adapter; it reads other modules only through service APIs."""

    def __init__(
        self,
        schema: "SchemaService",
        semantic: "SemanticService",
        metric: "MetricService",
        feedback: "FeedbackService",
        admin_id: int,
    ) -> None:
        self.schema, self.semantic, self.metric, self.feedback = schema, semantic, metric, feedback
        self.admin_id = admin_id

    async def catalog(self, datasource_id: int) -> list[IndexReference]:
        from app.modules.feedback.service import index_catalog as examples
        from app.modules.metric.service import index_catalog as metrics
        from app.modules.semantic.service import index_catalog as semantics

        refs = []
        for table in await self.schema.catalog(datasource_id):
            refs.append(
                IndexReference(
                    kind="table",
                    ref_id=table.id,
                    datasource_id=datasource_id,
                    actor_id=self.admin_id,
                )
            )
            refs.extend(
                IndexReference(
                    kind="column",
                    ref_id=column.id,
                    datasource_id=datasource_id,
                    actor_id=self.admin_id,
                )
                for column in table.columns
            )
        for kind in ("semantic", "metric", "example"):
            entries = (
                await semantics(self.semantic, datasource_id)
                if kind == "semantic"
                else await metrics(self.metric, datasource_id)
                if kind == "metric"
                else await examples(self.feedback, datasource_id)
            )
            refs.extend(
                IndexReference.model_validate(
                    {
                        "kind": kind,
                        "ref_id": ref_id,
                        "datasource_id": datasource_id,
                        "actor_id": actor_id,
                    }
                )
                for ref_id, actor_id in entries
            )
        return refs

    async def document(self, reference: IndexReference, actor: CurrentUser) -> IndexDocument | None:
        from app.modules.metric.service import render_template

        tables = await self.schema.catalog(reference.datasource_id)
        refs: dict[str, frozenset[str]] = {}
        ownership: dict[str, object] = {"scope": "platform", "status": "active"}
        text, version = "", 1
        if reference.kind in {"table", "column"}:
            for table in tables:
                if reference.kind == "table" and table.id == reference.ref_id:
                    refs[table.qualified_name] = frozenset(column.name for column in table.columns)
                    text = f"{table.qualified_name}: {table.comment} {table.description}"
                if reference.kind == "column":
                    for column in table.columns:
                        if column.id == reference.ref_id:
                            refs[table.qualified_name] = frozenset({column.name})
                            text = (
                                f"{table.qualified_name}.{column.name}: "
                                f"{column.comment} {column.description}"
                            )
        elif reference.kind == "semantic":
            term = await self.semantic.get(reference.ref_id, actor)
            ownership = term.model_dump(
                include={"scope", "status", "owner_team_id", "owner_user_id"}
            )
            target = await self.semantic.targets.resolve_target(
                term.datasource_id, term.target_type, term.target_id
            )
            target_table = next(
                (table for table in tables if table.qualified_name == target.table), None
            )
            if target_table is None:
                return None
            refs[target.table] = (
                frozenset({target.column})
                if target.column is not None
                else frozenset(column.name for column in target_table.columns)
            )
            text = f"{term.aliases}: {term.description} {term.enum_mapping}"
            version = term.version
        elif reference.kind == "metric":
            metric = await self.metric.get(reference.ref_id, actor)
            scope = await self.metric.permissions.visible_scope(actor, metric.datasource_id)
            datasource = await self.metric.datasources.resolve(metric.datasource_id, actor)
            guarded = self.metric.guard.check(
                render_template(metric.sql_template, metric.params), datasource.dialect, scope
            )
            refs = guarded.columns
            ownership = metric.model_dump(
                include={"scope", "status", "owner_team_id", "owner_user_id"}
            )
            text = f"{metric.name} {metric.aliases}: {metric.description} {metric.sql_template}"
            version = metric.version
        else:
            candidates = await self.feedback.examples(actor, reference.datasource_id)
            example = next((item for item in candidates if item.id == reference.ref_id), None)
            if example is None:
                return None
            scope = await self.feedback.permissions.visible_scope(actor, example.datasource_id)
            datasource = await self.feedback.datasources.resolve(example.datasource_id, actor)
            refs = self.feedback.guard.check(example.sql, datasource.dialect, scope).columns
            ownership = example.model_dump(
                include={"scope", "status", "owner_team_id", "owner_user_id"}
            )
            text, version = f"{example.question}: {example.sql}", example.version
        if not text:
            return None
        return IndexDocument.model_validate(
            {
                **ownership,
                "kind": reference.kind,
                "ref_id": reference.ref_id,
                "datasource_id": reference.datasource_id,
                "text": text,
                "references": refs,
                "version": version,
            }
        )


class OrgActors:
    def __init__(self, org: "OrgService") -> None:
        self.org = org

    async def get_actor(self, identifier: int) -> CurrentUser:
        return await self.org.identity(identifier)
