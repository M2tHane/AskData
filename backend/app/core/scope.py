from collections.abc import Iterable
from enum import StrEnum
from typing import Literal, TypeVar

from pydantic import BaseModel

from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode, PermissionDenied


class ScopeLevel(StrEnum):
    PLATFORM = "platform"
    TEAM = "team"
    PERSONAL = "personal"


class ScopeMixin(BaseModel):
    scope: ScopeLevel = ScopeLevel.PERSONAL
    owner_team_id: int | None = None
    owner_user_id: int | None = None
    status: Literal["private", "pending", "team", "active"] = "private"


T = TypeVar("T", bound=ScopeMixin)


class ScopedQuery:
    @staticmethod
    def visible_to(items: Iterable[T], user: CurrentUser) -> list[T]:
        return [
            item
            for item in items
            if (
                item.scope == ScopeLevel.PLATFORM
                or (
                    item.scope == ScopeLevel.TEAM
                    and (user.role == Role.ADMIN or item.owner_team_id == user.team_id)
                )
                or (item.scope == ScopeLevel.PERSONAL and item.owner_user_id == user.id)
            )
        ]

    @staticmethod
    def merge(items: Iterable[T], user: CurrentUser, key: str) -> list[T]:
        priority = {ScopeLevel.PLATFORM: 0, ScopeLevel.TEAM: 1, ScopeLevel.PERSONAL: 2}
        merged: dict[object, T] = {}
        for item in sorted(ScopedQuery.visible_to(items, user), key=lambda x: priority[x.scope]):
            merged[getattr(item, key)] = item
        return list(merged.values())


class PromotionService:
    @staticmethod
    def request(item: T, user: CurrentUser) -> T:
        if item.scope != ScopeLevel.PERSONAL or item.owner_user_id != user.id:
            raise PermissionDenied()
        if item.status != "private" or user.team_id is None:
            raise AppError(ErrorCode.CONFLICT)
        return item.model_copy(update={"status": "pending", "owner_team_id": user.team_id})

    @staticmethod
    def review(item: T, reviewer: CurrentUser, approved: bool, reason: str = "") -> T:
        if reviewer.role != Role.LEADER or item.owner_team_id != reviewer.team_id:
            raise PermissionDenied()
        if item.status != "pending" or (not approved and not reason.strip()):
            raise AppError(ErrorCode.CONFLICT)
        return item.model_copy(
            update={
                "status": "team" if approved else "private",
                "scope": ScopeLevel.TEAM if approved else ScopeLevel.PERSONAL,
            }
        )


__all__ = ["PromotionService", "ScopeLevel", "ScopeMixin", "ScopedQuery"]
