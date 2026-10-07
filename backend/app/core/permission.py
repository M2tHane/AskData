from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from app.core.deps import CurrentUser, Role
from app.core.errors import PermissionDenied
from app.core.masking import MaskRule


class RowOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    IN = "in"


class RowFilter(BaseModel):
    column: str
    operator: RowOperator = RowOperator.EQ
    value: str | int | float | bool | list[str | int] | None


class TableScope(BaseModel):
    # Complete metadata is essential for SELECT *; columns are the granted subset.
    all_columns: dict[str, str]
    columns: frozenset[str]
    masks: dict[str, MaskRule] = Field(default_factory=dict)
    row_filters: tuple[RowFilter, ...] = ()


class VisibleScope(BaseModel):
    model_config = ConfigDict(frozen=True)
    datasource_id: int = Field(ge=1)
    tables: dict[str, TableScope] = Field(default_factory=dict)


class TableGrant(BaseModel):
    name: str
    all_columns: dict[str, str]
    team_columns: frozenset[str] = frozenset()
    member_columns: frozenset[str] = frozenset()
    team_visible: bool = False
    member_visible: bool = False
    masks: dict[str, MaskRule] = Field(default_factory=dict)
    row_filters: tuple[RowFilter, ...] = ()


class PermissionProvider(Protocol):
    async def snapshot(self, datasource_id: int, team_id: int | None) -> list[TableGrant]: ...


class ScopeCache(Protocol):
    async def revision(self, datasource_id: int) -> str: ...
    async def get(
        self, user: CurrentUser, datasource_id: int, revision: str
    ) -> VisibleScope | None: ...
    async def put(self, user: CurrentUser, scope: VisibleScope, revision: str) -> None: ...
    async def invalidate(self, datasource_id: int) -> None: ...


class RedisScopeCache:
    """Old snapshots only populate their original generation, never a newer authorization epoch."""

    def __init__(self, redis: Redis, ttl: int = 300) -> None:
        self.redis = redis
        self.ttl = ttl

    async def revision(self, datasource_id: int) -> str:
        value = await self.redis.get(f"scope-generation:{datasource_id}") or b"0"
        return value.decode() if isinstance(value, bytes) else str(value)

    @staticmethod
    def _key(user: CurrentUser, datasource_id: int, revision: str) -> str:
        return f"scope:{datasource_id}:{revision}:{user.id}:{user.role}:{user.team_id}"

    async def get(
        self, user: CurrentUser, datasource_id: int, revision: str
    ) -> VisibleScope | None:
        value = await self.redis.get(self._key(user, datasource_id, revision))
        return VisibleScope.model_validate_json(value) if value else None

    async def put(self, user: CurrentUser, scope: VisibleScope, revision: str) -> None:
        await self.redis.set(
            self._key(user, scope.datasource_id, revision), scope.model_dump_json(), ex=self.ttl
        )

    async def invalidate(self, datasource_id: int) -> None:
        await self.redis.incr(f"scope-generation:{datasource_id}")


class PermissionService:
    def __init__(self, provider: PermissionProvider, cache: ScopeCache | None = None) -> None:
        self.provider = provider
        self.cache = cache

    async def visible_scope(self, user: CurrentUser, datasource_id: int) -> VisibleScope:
        if not user.active:
            raise PermissionDenied()
        for _ in range(3):
            revision = await self.cache.revision(datasource_id) if self.cache else "0"
            cached = await self.cache.get(user, datasource_id, revision) if self.cache else None
            if cached is None:
                grants = await self.provider.snapshot(datasource_id, user.team_id)
                cached = self._compute(user, datasource_id, grants)
                if self.cache:
                    await self.cache.put(user, cached, revision)
            if cached.datasource_id != datasource_id:
                raise PermissionDenied()
            if self.cache is None or await self.cache.revision(datasource_id) == revision:
                return cached.model_copy(deep=True)
        raise PermissionDenied()

    @staticmethod
    def _compute(user: CurrentUser, datasource_id: int, grants: list[TableGrant]) -> VisibleScope:
        tables: dict[str, TableScope] = {}
        for grant in grants:
            columns: frozenset[str]
            if user.role == Role.ADMIN:
                columns = frozenset(grant.all_columns)
            elif not grant.team_visible:
                continue
            elif user.role == Role.LEADER:
                columns = grant.team_columns & frozenset(grant.all_columns)
            elif grant.member_visible:
                columns = grant.member_columns & grant.team_columns & frozenset(grant.all_columns)
            else:
                continue
            if columns:
                tables[grant.name] = TableScope(
                    all_columns=grant.all_columns,
                    columns=columns,
                    masks={key: rule for key, rule in grant.masks.items() if key in columns},
                    row_filters=() if user.role == Role.ADMIN else grant.row_filters,
                )
        return VisibleScope(datasource_id=datasource_id, tables=tables)

    async def invalidate(self, datasource_id: int) -> None:
        if self.cache:
            await self.cache.invalidate(datasource_id)
