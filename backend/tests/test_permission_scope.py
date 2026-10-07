import pytest

from app.core.deps import CurrentUser, Role
from app.core.errors import AppError
from app.core.masking import MaskKind, MaskRule
from app.core.permission import PermissionService, RowFilter, TableGrant
from app.core.scope import PromotionService, ScopedQuery, ScopeLevel, ScopeMixin
from tests.fakes import FakePermissions, MemoryScopeCache


@pytest.fixture
def grants():
    return [
        TableGrant(
            name="orders",
            all_columns={"id": "INT", "phone": "TEXT", "secret": "TEXT"},
            team_visible=True,
            member_visible=True,
            team_columns={"id", "phone"},
            member_columns={"id", "phone", "secret", "removed"},
            masks={"phone": MaskRule(kind=MaskKind.PHONE)},
            row_filters=(RowFilter(column="id", value=7),),
        ),
        TableGrant(name="new_table", all_columns={"id": "INT"}),
    ]


async def test_member_is_team_subset_and_keeps_masks(member, grants):
    service = PermissionService(FakePermissions(grants))
    scope = await service.visible_scope(member, 1)
    assert set(scope.tables) == {"orders"}
    assert scope.tables["orders"].columns == {"id", "phone"}
    assert scope.tables["orders"].masks["phone"].kind == MaskKind.PHONE
    assert scope.tables["orders"].row_filters
    grants[0].all_columns.pop("phone")
    scope = await service.visible_scope(member, 1)
    assert scope.tables["orders"].columns == {"id"}


@pytest.mark.parametrize("role", [Role.LEADER, Role.ADMIN])
async def test_role_scope(grants, role):
    user = CurrentUser(id=1, role=role, team_id=1)
    scope = await PermissionService(FakePermissions(grants)).visible_scope(user, 1)
    if role == Role.ADMIN:
        assert set(scope.tables) == {"orders", "new_table"}
        assert "secret" in scope.tables["orders"].columns
        assert not scope.tables["orders"].row_filters
    else:
        assert set(scope.tables) == {"orders"}
        assert scope.tables["orders"].columns == {"id", "phone"}


async def test_cache_invalidation_and_mutation_isolation(member, grants):
    provider, cache = FakePermissions(grants), MemoryScopeCache()
    service = PermissionService(provider, cache)
    scope = await service.visible_scope(member, 1)
    scope.tables.clear()
    assert (await service.visible_scope(member, 1)).tables
    assert provider.calls == 1
    grants[0].team_columns = frozenset({"id"})
    await service.invalidate(1)
    assert (await service.visible_scope(member, 1)).tables["orders"].columns == {"id"}
    assert provider.calls == 2


async def test_invalidation_during_snapshot_does_not_cache_old_permissions(member, grants):
    provider, cache = FakePermissions(grants), MemoryScopeCache()

    async def revoke():
        provider.on_snapshot = None
        grants[0].team_columns = frozenset({"id"})
        await cache.invalidate(1)

    provider.on_snapshot = revoke
    scope = await PermissionService(provider, cache).visible_scope(member, 1)
    assert scope.tables["orders"].columns == {"id"}
    assert provider.calls == 2


async def test_inactive_identity_is_denied(member, grants):
    with pytest.raises(AppError):
        await PermissionService(FakePermissions(grants)).visible_scope(
            member.model_copy(update={"active": False}),
            1,
        )


def test_personal_visibility_even_admin_cannot_see_other_person(member):
    private = ScopeMixin(owner_user_id=member.id, owner_team_id=1)
    team = ScopeMixin(scope=ScopeLevel.TEAM, owner_team_id=1, status="team")
    platform = ScopeMixin(scope=ScopeLevel.PLATFORM, status="active")
    other = ScopeMixin(owner_user_id=99, owner_team_id=1)
    items = [private, team, platform, other]
    assert ScopedQuery.visible_to(items, member) == [private, team, platform]
    admin = CurrentUser(id=1, role=Role.ADMIN)
    assert ScopedQuery.visible_to(items, admin) == [team, platform]
    leader = CurrentUser(id=2, role=Role.LEADER, team_id=1)
    assert ScopedQuery.visible_to(items, leader) == [team, platform]


def test_scope_priority(member):
    class Term(ScopeMixin):
        name: str

    platform = Term(name="revenue", scope=ScopeLevel.PLATFORM, status="active")
    team = Term(name="revenue", scope=ScopeLevel.TEAM, owner_team_id=1, status="team")
    personal = Term(name="revenue", owner_user_id=member.id)
    assert ScopedQuery.merge([personal, platform, team], member, "name") == [personal]


def test_promotion_state_machine(member):
    item = ScopeMixin(owner_user_id=member.id)
    leader = CurrentUser(id=2, role=Role.LEADER, team_id=1)
    pending = PromotionService.request(item, member)
    assert pending.status == "pending" and item.status == "private"
    with pytest.raises(AppError):
        PromotionService.request(pending, member)
    with pytest.raises(AppError):
        PromotionService.review(pending, leader, False)
    assert PromotionService.review(pending, leader, False, "needs revision").status == "private"
    approved = PromotionService.review(pending, leader, True)
    assert approved.scope == ScopeLevel.TEAM and approved.status == "team"
    with pytest.raises(AppError):
        PromotionService.review(pending, CurrentUser(id=1, role=Role.ADMIN), True)
    with pytest.raises(AppError):
        PromotionService.review(pending, CurrentUser(id=9, role=Role.LEADER, team_id=2), True)
