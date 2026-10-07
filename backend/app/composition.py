"""Application composition. Sessions and services are scoped to one request or job."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import unquote, urlsplit

from arq.connections import ArqRedis
from fastapi import FastAPI, Request
from minio import Minio
from pydantic import Field, SecretStr
from sqlalchemy import Table

from app.core.audit import (
    AuditLog,
    AuditService,
    DatabaseAuditStore,
    DatabaseQueryLogStore,
    QueryLogWriter,
    QueryRun,
)
from app.core.config import Settings
from app.core.crypto import SecretBox
from app.core.datasource import ConnectorRegistry, Datasource, DatasourceInspector
from app.core.db import AsyncSession, Database
from app.core.deps import Role, identity_resolver
from app.core.errors import AppError, ErrorCode
from app.core.importer import (
    ImportGateway,
    ImportReadGateway,
    ImportSettings,
    PostgresImportGateway,
)
from app.core.llm import (
    EmbeddingClient,
    LLMClient,
    ModelConfig,
    ModelRegistry,
    ModelTransport,
    RerankClient,
)
from app.core.permission import PermissionService, RedisScopeCache, TableGrant
from app.core.retention import DatabaseRetentionStore, RetentionService
from app.core.sql.dialect import Dialect
from app.core.sql.executor import QueryLimits, RedisQueryLimiter, SqlExecutor
from app.core.sql.guard import SqlGuard
from app.core.storage import MinioStore
from app.core.tasks import (
    ArqTaskQueue,
    DatabaseTaskStore,
    TaskContext,
    TaskHandler,
    TaskManager,
    TaskRun,
)
from app.modules.auth import service as auth
from app.modules.chat import service as chat
from app.modules.chat.pipeline import (
    ContextBuilder,
    PipelineDependencies,
    QueryPipeline,
)
from app.modules.chat.repository import ChatRepository
from app.modules.chat.retrieval import ServiceRetrievalIndex
from app.modules.datasource import service as datasource
from app.modules.embedding.repository import EmbeddingRepository
from app.modules.embedding.service import EmbeddingService, OrgActors, ServiceIndexSource
from app.modules.eval.assets import EvalAssets
from app.modules.eval.repository import EvalRepository
from app.modules.eval.schemas import EvalCaseInput, PipelineOutcome, RunRequest
from app.modules.eval.service import EvalQuestion, EvalService
from app.modules.feedback import service as feedback
from app.modules.metric import service as metric
from app.modules.modelcfg import service as modelcfg
from app.modules.org import service as org
from app.modules.permission import service as permission
from app.modules.promotion import service as promotion
from app.modules.querylog import service as querylog
from app.modules.schema import service as schema
from app.modules.semantic import service as semantic
from app.modules.upload.repository import UploadRepository
from app.modules.upload.service import UploadService
from app.modules.usage import service as usage


class RuntimeSettings(ImportSettings):
    model_config = {**ImportSettings.model_config, "env_ignore_empty": True}
    import_reader_host: str = "pg-import"
    import_reader_port: int = 5432
    import_reader_database: str = "askdata_import"
    eval_assets_path: Path = Path(__file__).resolve().parents[2] / "eval"
    scheduler_admin_id: int | None = Field(default=None, ge=1)
    eval_team_ids: dict[str, int] = Field(default_factory=dict)
    eval_user_ids: dict[str, int] = Field(default_factory=dict)
    eval_datasource_ids: dict[str, int] = Field(default_factory=dict)
    eval_semantic_aliases: dict[str, str] = Field(default_factory=dict)
    eval_metric_aliases: dict[str, str] = Field(default_factory=dict)


class AdapterFactory(Protocol):
    def __call__(self, **dependencies: Any) -> Any: ...


def adapter_factory(module: str, name: str) -> AdapterFactory:
    """Resolve I2 adapters at use time so API/OpenAPI do not require fixture setup."""
    try:
        return cast(AdapterFactory, getattr(import_module(module), name))
    except (ModuleNotFoundError, AttributeError):
        raise AppError(ErrorCode.INTERNAL_ERROR, "Integration adapter unavailable", 503) from None


class PinnedModel:
    def __init__(self, model: ModelConfig) -> None:
        self.model = model

    async def configurations(self) -> list[ModelConfig]:
        return [self.model]

    async def preference(self, team_id: int | None, kind: str) -> str | None:
        return self.model.id

    async def default(self, kind: str) -> str | None:
        return self.model.id


class RuntimeGrants:
    def __init__(self, runtime: "Composition") -> None:
        self.runtime = runtime

    async def snapshot(self, datasource_id: int, team_id: int | None) -> list[TableGrant]:
        async with self.runtime.services(transaction=True) as services:
            return await services.grants.snapshot(datasource_id, team_id)


class PendingUploadGrants:
    """Pending upload sources have no query scope until publication activates them."""

    def __init__(self, services: "Services") -> None:
        self.services = services

    async def snapshot(self, datasource_id: int, team_id: int | None) -> list[TableGrant]:
        try:
            return await self.services.grants.snapshot(datasource_id, team_id)
        except AppError as error:
            if error.code != ErrorCode.DATASOURCE_UNAVAILABLE:
                raise
            # Only the upload service may use its typed pending-source lifecycle.
            await self.services.datasource.upload_owner(datasource_id)
            return []


class RuntimeAssets(EvalAssets):
    def __init__(self, runtime: "Composition") -> None:
        super().__init__(runtime.settings.eval_assets_path)
        self.runtime = runtime

    async def hidden_names(self, case: EvalCaseInput) -> set[str]:
        assets = await self.runtime.mapped_assets()
        return cast(set[str], await assets.hidden_names(case))


class RuntimeEvaluation:
    def __init__(self, runtime: "Composition") -> None:
        self.runtime = runtime

    async def run(self, question: EvalQuestion, config: RunRequest) -> PipelineOutcome:
        assets = await self.runtime.mapped_assets()
        async with self.runtime.services() as services:

            async def dependencies(
                question: EvalQuestion, config: RunRequest
            ) -> PipelineDependencies:
                deps = services.chat().pipeline.deps
                model = await services.models.registry.get(config.model_id, "llm")
                deps.llm = LLMClient(
                    ModelRegistry(PinnedModel(model)), services.usage, self.runtime.transport
                )
                threshold = config.config.get("retrieval_threshold", 30)
                if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
                    raise AppError(ErrorCode.VALIDATION_ERROR)
                deps.builder.threshold = threshold
                return deps

            adapter = adapter_factory("app.modules.eval.adapters", "build_eval_adapter")(
                assets=assets, dependencies=dependencies
            )
            outcome = await adapter.run(question, config)
            await services.session.commit()
            return cast(PipelineOutcome, outcome)

    async def result(self, case: EvalCaseInput) -> Any:
        assets = await self.runtime.mapped_assets()
        runner = adapter_factory("app.modules.eval.adapters", "build_reference_runner")(
            assets=assets, guard=SqlGuard(), executor=self.runtime.executor
        )
        return await runner.result(case)


class Services:
    """One session shared by business services; no service escapes its scope."""

    def __init__(self, runtime: "Composition", session: AsyncSession, *, admin_id: int = 0) -> None:
        self.runtime, self.session = runtime, session
        box = runtime.box
        if box is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
        self.audit = AuditService(DatabaseAuditStore(session), runtime.settings.retention_days)
        self.org = org.build_service(session)
        self.auth = auth.build_service(session, runtime.settings)
        self.datasource = datasource.build_service(session, box, runtime.inspector, runtime.cache)
        self.schema = schema.build_service(session, box, runtime.inspector)
        self.grants = permission.build_service(session, box, runtime.cache)
        self.permissions = self.grants.permissions
        self.usage = usage.build_service(session, runtime.database)
        self.models = modelcfg.build_service(session, box, runtime.transport, self.usage)
        self.llm = LLMClient(self.models.registry, self.usage, runtime.transport)
        self.embeddings = EmbeddingClient(self.models.registry, self.usage, runtime.transport)
        self.datasources = metric.QueryDatasources(self.datasource, self.permissions)
        self.semantic = semantic.build_service(
            session, self.permissions, semantic.SchemaTargets(self.schema)
        )
        self.metric = metric.build_service(
            session, self.permissions, self.datasources, runtime.executor
        )
        self.querylog = querylog.build_service(session)
        self.feedback = feedback.build_service(
            session, self.querylog, self.permissions, self.datasources, None
        )
        self.promotion = promotion.build_service(session, self.semantic, self.metric)
        self.embedding = EmbeddingService(
            EmbeddingRepository(session),
            self.permissions,
            self.embeddings,
            ServiceIndexSource(self.schema, self.semantic, self.metric, self.feedback, admin_id),
            OrgActors(self.org),
            self.audit,
        )

    def chat(self) -> chat.ChatService:
        repository = ChatRepository(self.session)
        dependencies = PipelineDependencies(
            self.permissions,
            self.datasources.resolve,
            repository.history,
            ContextBuilder(
                ServiceRetrievalIndex(
                    self.schema,
                    self.semantic,
                    self.metric,
                    self.feedback,
                    self.embedding,
                    RerankClient(self.models.registry, self.usage, self.runtime.transport),
                )
            ),
            self.llm,
            SqlGuard(),
            self.runtime.executor,
            QueryLogWriter(
                DatabaseQueryLogStore(self.session), self.runtime.settings.retention_days
            ),
            limits=QueryLimits(
                user_id=1,
                timeout_seconds=self.runtime.settings.query_timeout_seconds,
                max_rows=self.runtime.settings.max_query_rows,
                max_cost=self.runtime.settings.max_query_cost,
            ),
        )
        return chat.ChatService(repository, QueryPipeline(dependencies), self.audit)

    def upload(self) -> UploadService:
        runtime = self.runtime
        if runtime.storage is None or runtime.importer is None:
            raise AppError(ErrorCode.TASK_FAILED, http_status=503)
        registration = adapter_factory("app.modules.upload.adapters", "build_upload_registration")(
            datasources=self.datasource,
            schema=self.schema,
            grants=self.grants,
            org=self.org,
            permissions=self.permissions,
        )
        return UploadService(
            UploadRepository(self.session),
            runtime.storage,
            runtime.importer,
            registration,
            PermissionService(PendingUploadGrants(self), runtime.cache),
            self.audit,
            runtime.tasks,
        )

    def evaluation(self) -> EvalService:
        adapter = RuntimeEvaluation(self.runtime)
        return EvalService(
            EvalRepository(self.session),
            RuntimeAssets(self.runtime),
            adapter,
            adapter,
            self.audit,
            self.runtime.tasks,
        )


class Composition:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        redis: ArqRedis,
        *,
        worker: bool = False,
        transport: ModelTransport | None = None,
    ) -> None:
        self.settings = RuntimeSettings(**settings.model_dump())
        self.database, self.redis, self.transport = database, redis, transport
        self.box = SecretBox(settings.encryption_key) if settings.encryption_key else None
        self.connectors = ConnectorRegistry()
        self.inspector = DatasourceInspector(self.connectors)
        self.cache = RedisScopeCache(redis)
        self.executor = SqlExecutor(self.connectors, RedisQueryLimiter(redis))
        self.storage = None
        if settings.minio_access_key and settings.minio_secret_key:
            self.storage = MinioStore(
                Minio(
                    settings.minio_endpoint,
                    access_key=settings.minio_access_key.get_secret_value(),
                    secret_key=settings.minio_secret_key.get_secret_value(),
                    secure=settings.minio_secure,
                ),
                settings.minio_bucket,
            )
        self.importer: ImportGateway | None = None
        if self.settings.import_reader_password:
            reader = Datasource(
                id=0,
                dialect=Dialect.POSTGRES,
                host=self.settings.import_reader_host,
                port=self.settings.import_reader_port,
                database=self.settings.import_reader_database,
                username="unused_reader_template",
                password=self.settings.import_reader_password,
                tls=False,
            )
            self.importer = ImportReadGateway(reader)
            if worker and self.settings.import_writer_url:
                url = urlsplit(self.settings.import_writer_url.get_secret_value())
                writer = reader.model_copy(
                    update={
                        "host": url.hostname or reader.host,
                        "port": url.port or reader.port,
                        "database": unquote(url.path.lstrip("/")),
                        "username": unquote(url.username or ""),
                        "password": SecretStr(unquote(url.password or "")),
                        "read_only": False,
                    }
                )
                self.importer = PostgresImportGateway(writer, self.settings.import_reader_password)
        self.tasks = TaskManager(
            DatabaseTaskStore(database.sessions), ArqTaskQueue(redis), self.handlers()
        )

    async def mapped_assets(self) -> Any:
        settings = self.settings
        if (
            not settings.eval_team_ids
            or not settings.eval_user_ids
            or not settings.eval_datasource_ids
        ):
            raise AppError(ErrorCode.TASK_FAILED, "Evaluation fixture bindings are required", 503)
        async with self.services(transaction=True) as services:
            for identifier in settings.eval_team_ids.values():
                await services.org.ensure_team(identifier)
            users = {
                alias: await services.org.identity(identifier)
                for alias, identifier in settings.eval_user_ids.items()
            }
            datasources = {
                alias: await services.datasource.connection(identifier)
                for alias, identifier in settings.eval_datasource_ids.items()
            }
        from app.modules.eval.adapters import FixtureBindings

        bindings = FixtureBindings(
            team_ids=settings.eval_team_ids,
            users=users,
            datasources=datasources,
            semantic_aliases=settings.eval_semantic_aliases,
            metric_aliases=settings.eval_metric_aliases,
        )
        return adapter_factory("app.modules.eval.adapters", "build_fixture_assets")(
            directory=settings.eval_assets_path,
            bindings=bindings,
            permissions=PermissionService(RuntimeGrants(self), self.cache),
        )

    @asynccontextmanager
    async def services(
        self, *, transaction: bool = False, admin_id: int = 0
    ) -> AsyncIterator[Services]:
        context = self.database.transaction() if transaction else self.database.session()
        async with context as session:
            yield Services(self, session, admin_id=admin_id)
        for callback in session.info.get("after_commit", []):
            await callback()

    def handlers(self) -> dict[str, TaskHandler]:
        async def sync(context: TaskContext) -> None:
            if self.box is None:
                raise AppError(ErrorCode.TASK_FAILED, http_status=503)
            await schema.sync_handler(self.database, self.box, self.cache, self.inspector)(context)
            async with self.services(transaction=True) as services:
                actor = await services.org.identity(int(context.task.payload["actor_id"]))
                try:
                    await services.models.registry.select(actor.team_id, "embedding")
                except AppError as error:
                    if error.code != ErrorCode.LLM_UNAVAILABLE:
                        raise
                else:
                    await services.embedding.enqueue(
                        int(context.task.payload["datasource_id"]),
                        self.tasks,
                        actor=actor,
                        rebuild=True,
                    )

        async def upload(context: TaskContext) -> None:
            async with self.services(transaction=True) as services:
                actor = await services.org.identity(int(context.task.payload["actor"]["id"]))
                if actor.role not in {Role.ADMIN, Role.LEADER}:
                    raise AppError(ErrorCode.FORBIDDEN)
                context.task.payload["actor"] = actor.model_dump(mode="json")
            async with self.services() as services:
                await services.upload().run_import(context)

        async def evaluation(context: TaskContext) -> None:
            async with self.services(transaction=True) as services:
                actor = await services.org.identity(int(context.task.payload["actor"]["id"]))
                if actor.role != Role.ADMIN:
                    raise AppError(ErrorCode.FORBIDDEN)
                context.task.payload["actor"] = actor.model_dump(mode="json")
            async with self.services() as services:
                await services.evaluation().run(context)

        async def update(context: TaskContext) -> None:
            async with self.services(
                transaction=True, admin_id=int(context.task.payload["actor_id"])
            ) as services:
                await services.embedding.update_task(context)

        async def rebuild(context: TaskContext) -> None:
            async with self.services(
                transaction=True, admin_id=int(context.task.payload["actor_id"])
            ) as services:
                await services.embedding.rebuild_task(context)

        async def retention(context: TaskContext) -> None:
            async with self.database.transaction() as session:
                await RetentionService(
                    DatabaseRetentionStore(
                        session,
                        [cast(Table, model.__table__) for model in (AuditLog, QueryRun, TaskRun)],
                    )
                ).run()
            await context.progress(100)

        return {
            schema.SYNC_TASK: sync,
            "upload.import": upload,
            "eval.run": evaluation,
            EmbeddingService.UPDATE_TASK: update,
            EmbeddingService.REBUILD_TASK: rebuild,
            "retention": retention,
        }


def configure_dependencies(application: FastAPI) -> None:
    async def transactional_services(request: Request) -> AsyncIterator[Services]:
        runtime: Composition | None = getattr(request.app.state, "composition", None)
        if runtime is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
        async with runtime.services(transaction=True) as services:
            yield services

    # Use one cached dependency per request for auth and all transaction-scoped APIs.
    from typing import Annotated

    from fastapi import Depends

    ServiceDependency = Annotated[Services, Depends(transactional_services, scope="function")]

    def dependency(attribute: str) -> Any:
        async def resolve(services: ServiceDependency) -> Any:
            return getattr(services, attribute)

        return resolve

    for module, attribute in (
        (auth, "auth"),
        (org, "org"),
        (datasource, "datasource"),
        (schema, "schema"),
        (permission, "grants"),
        (modelcfg, "models"),
    ):
        application.dependency_overrides[module.get_service] = dependency(attribute)
    application.dependency_overrides[identity_resolver] = dependency("auth")
    application.dependency_overrides[schema.get_permissions] = dependency("permissions")
    for name in ("semantic", "metric", "promotion", "feedback", "querylog", "usage", "embedding"):
        module = import_module(f"app.modules.{name}.api")
        application.dependency_overrides[module.get_service] = dependency(name)

    from app.modules.audit import api as audit_api
    from app.modules.audit.service import build_service as build_audit

    async def audit_service(services: ServiceDependency) -> Any:
        return build_audit(services.session)

    application.dependency_overrides[audit_api.get_service] = audit_service

    def independent_dependency(attribute: str) -> Any:
        async def resolve(request: Request) -> AsyncIterator[Any]:
            runtime: Composition | None = getattr(request.app.state, "composition", None)
            if runtime is None:
                raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
            async with runtime.services() as services:
                yield getattr(services, attribute)()

        return resolve

    from app.modules.eval.api import eval_service
    from app.modules.upload.api import upload_service

    application.dependency_overrides[chat.get_service] = independent_dependency("chat")
    application.dependency_overrides[upload_service] = independent_dependency("upload")
    application.dependency_overrides[eval_service] = independent_dependency("evaluation")
