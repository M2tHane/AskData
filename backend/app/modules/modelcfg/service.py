from typing import Annotated

from fastapi import Depends, Request

from app.core.audit import AuditService
from app.core.crypto import SecretBox
from app.core.db import AsyncSession
from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied
from app.core.llm import (
    CallContext,
    CallRecord,
    ChatMessage,
    EmbeddingClient,
    LLMClient,
    ModelConfig,
    ModelRegistry,
    ModelTransport,
    RerankClient,
    UsageRecorder,
)
from app.core.prompt import PromptId, PromptRegistry
from app.core.response import Page, PageParams
from app.modules.auth.service import audit_for, get_session, require_admin, secret_box, settings_for
from app.modules.modelcfg.repository import ModelRepository
from app.modules.modelcfg.schemas import (
    ModelKind,
    ModelRead,
    ModelTestResult,
    ModelWrite,
    PreferenceRead,
)
from app.modules.org.service import OrgService
from app.modules.org.service import build_service as build_org


class _PinnedModel:
    def __init__(self, model: ModelConfig) -> None:
        self.model = model

    async def configurations(self) -> list[ModelConfig]:
        return [self.model]

    async def preference(self, team_id: int | None, kind: str) -> str | None:
        return self.model.id

    async def default(self, kind: str) -> str | None:
        return self.model.id


class _TestUsage:
    def __init__(self, audit: AuditService, actor: CurrentUser) -> None:
        self.audit, self.actor = audit, actor

    async def authorize(self, context: CallContext) -> None:
        require_admin(self.actor)

    async def record(self, record: CallRecord) -> None:
        await self.audit.record(
            self.actor,
            "model.test.call",
            ("model_config", record.model_id),
            {
                "success": record.success,
                "tokens_in": record.usage.tokens_in,
                "tokens_out": record.usage.tokens_out,
            },
        )


class ModelConfigService:
    def __init__(
        self,
        repository: ModelRepository,
        audit: AuditService,
        box: SecretBox,
        org: OrgService,
        transport: ModelTransport | None = None,
        recorder: UsageRecorder | None = None,
    ) -> None:
        self.repository, self.audit, self.box, self.org = repository, audit, box, org
        self.transport, self.recorder = transport, recorder
        self.registry = ModelRegistry(self)

    async def configurations(self) -> list[ModelConfig]:
        return [
            self._config(ModelRead.model_validate(row), row.api_key_enc)
            for row in await self.repository.all()
            if row.enabled
        ]

    def _config(self, row: ModelRead, encrypted_key: str) -> ModelConfig:
        return ModelConfig(
            id=str(row.id),
            kind=row.type,
            model=f"{row.provider}/{row.model_name}",
            api_key=self.box.decrypt(encrypted_key),
            base_url=row.base_url,
            enabled=row.enabled,
            **row.params.model_dump(),
        )

    async def preference(self, team_id: int | None, kind: str) -> str | None:
        identifier = await self.repository.preference(team_id, kind)
        return str(identifier) if identifier is not None else None

    async def default(self, kind: str) -> str | None:
        rows = [row for row in await self.repository.all() if row.enabled and row.type == kind]
        return next(
            (str(row.id) for row in rows if row.is_default), str(rows[0].id) if rows else None
        )

    async def list(self, page: PageParams, *, actor: CurrentUser) -> Page[ModelRead]:
        if actor.role not in {Role.ADMIN, Role.LEADER} or not actor.active:
            raise PermissionDenied()
        rows = await (
            self.repository.list(page.offset, page.page_size)
            if actor.role == Role.ADMIN
            else self.repository.enabled_list(page.offset, page.page_size)
        )
        return Page(
            items=[ModelRead.model_validate(row) for row in rows],
            total=await self.repository.count(enabled_only=actor.role != Role.ADMIN),
            page=page.page,
            page_size=page.page_size,
        )

    async def _validate(self, data: ModelWrite, target_id: int | None = None) -> None:
        if data.is_default and not data.enabled:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if (data.type == "embedding") != (data.embedding_dimension is not None):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if data.type == "embedding":
            if data.embedding_dimension != 1024:
                raise AppError(ErrorCode.CONFLICT)
            # Changing dimensions requires the separately reviewed vector migration.
            if any(
                row.type == "embedding"
                and row.id != target_id
                and row.embedding_dimension != data.embedding_dimension
                for row in await self.repository.all()
            ):
                raise AppError(ErrorCode.CONFLICT)

    async def create(self, data: ModelWrite, *, actor: CurrentUser) -> ModelRead:
        require_admin(actor)
        await self._validate(data)
        if data.is_default:
            await self.repository.clear_default(data.type)
        values = data.model_dump(exclude={"api_key"})
        values["api_key_enc"] = self.box.encrypt(data.api_key)
        row = await self.repository.create(values, actor.id)
        await self.audit.record(actor, "model.create", ("model_config", str(row.id)))
        return ModelRead.model_validate(row)

    async def update(self, target_id: int, data: ModelWrite, *, actor: CurrentUser) -> ModelRead:
        require_admin(actor)
        row = await self.repository.get(target_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        if row.type != data.type or (
            row.type == "embedding" and row.embedding_dimension != data.embedding_dimension
        ):
            raise AppError(ErrorCode.CONFLICT)
        if not data.enabled and await self.repository.used(target_id):
            raise AppError(ErrorCode.CONFLICT)
        await self._validate(data, target_id)
        if data.is_default:
            await self.repository.clear_default(data.type)
        values = data.model_dump(exclude={"api_key"})
        values["api_key_enc"] = self.box.encrypt(data.api_key)
        await self.repository.update(row, values)
        await self.audit.record(actor, "model.update", ("model_config", str(row.id)))
        return ModelRead.model_validate(row)

    async def set_preference(
        self, team_id: int, kind: ModelKind, model_id: int, *, actor: CurrentUser
    ) -> PreferenceRead:
        if not actor.active or (
            actor.role != Role.ADMIN and (actor.role != Role.LEADER or team_id != actor.team_id)
        ):
            raise PermissionDenied()
        await self.org.ensure_team(team_id)
        await self.registry.get(str(model_id), kind)
        await self.repository.set_preference(team_id, kind, model_id, actor.id)
        await self.audit.record(actor, "model.preference", ("team", str(team_id)), {"type": kind})
        return PreferenceRead(team_id=team_id, type=kind, model_id=model_id)

    async def get_preference(
        self, team_id: int, kind: ModelKind, *, actor: CurrentUser
    ) -> PreferenceRead:
        if not actor.active or (
            actor.role != Role.ADMIN and (actor.role != Role.LEADER or team_id != actor.team_id)
        ):
            raise PermissionDenied()
        await self.org.ensure_team(team_id)
        model = await self.registry.select(team_id, kind)
        return PreferenceRead(team_id=team_id, type=kind, model_id=int(model.id))

    async def test(self, target_id: int, *, actor: CurrentUser) -> ModelTestResult:
        require_admin(actor)
        row = await self.repository.get(target_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        model = self._config(ModelRead.model_validate(row), row.api_key_enc)
        model.enabled = True  # Administrators can test a configuration before enabling it.
        registry = ModelRegistry(_PinnedModel(model))
        usage = self.recorder or _TestUsage(self.audit, actor)
        prompt = PromptRegistry().render(PromptId.EXPLAIN, {"connection_test": "Reply OK"})
        context = CallContext(
            user_id=actor.id,
            team_id=actor.team_id,
            prompt_version=f"{prompt.template}:{prompt.version}",
        )
        available = True
        try:
            if model.kind == "llm":
                await LLMClient(registry, usage, self.transport).chat(
                    [ChatMessage(role="user", content=prompt.content)], context
                )
            elif model.kind == "embedding":
                vectors = await EmbeddingClient(registry, usage, self.transport).embed(
                    ["test"], context
                )
                available = bool(vectors and len(vectors[0]) == row.embedding_dimension)
            else:
                await RerankClient(registry, usage, self.transport).rerank(
                    "test", ["test"], context
                )
        except AppError as error:
            if error.code != ErrorCode.LLM_UNAVAILABLE:
                raise
            available = False
        await self.audit.record(
            actor, "model.test", ("model_config", str(target_id)), {"available": available}
        )
        return ModelTestResult(available=available)


def build_service(
    session: AsyncSession,
    box: SecretBox,
    transport: ModelTransport | None = None,
    recorder: UsageRecorder | None = None,
) -> ModelConfigService:
    return ModelConfigService(
        ModelRepository(session), audit_for(session), box, build_org(session), transport, recorder
    )


def get_service(
    request: Request, session: Annotated[AsyncSession, Depends(get_session, scope="function")]
) -> ModelConfigService:
    return build_service(
        session,
        secret_box(settings_for(request)),
        getattr(request.app.state, "model_transport", None),
        getattr(request.app.state, "usage_recorder", None),
    )
