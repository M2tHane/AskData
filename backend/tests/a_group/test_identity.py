from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.audit import AuditLog
from app.core.errors import AppError, ErrorCode
from app.modules.auth.models import RefreshToken
from app.modules.auth.schemas import LoginRequest
from app.modules.org.schemas import TeamWrite, UserUpdate, UserWrite


async def test_login_refresh_rotation_replay_logout_and_identity(identity_db):
    world = identity_db
    async with world.database.transaction() as session:
        auth = world.auth(session)
        pair = await auth.login(
            LoginRequest(username="member", password=SecretStr("member-password"))
        )
        assert await auth.resolve(pair.access_token) == world.member
        assert pair.token_type == "bearer" and pair.expires_in == 900
    async with world.database.transaction() as session:
        auth = world.auth(session)
        replacement = await auth.rotate(SecretStr(pair.refresh_token))
        assert replacement.refresh_token != pair.refresh_token
    async with world.database.transaction() as session:
        with pytest.raises(AppError) as error:
            await world.auth(session).rotate(SecretStr(pair.refresh_token))
        assert error.value.code == ErrorCode.AUTH_FAILED
    async with world.database.transaction() as session:
        await world.auth(session).logout(SecretStr(replacement.refresh_token), actor=world.member)
    async with world.database.transaction() as session:
        with pytest.raises(AppError):
            await world.auth(session).rotate(SecretStr(replacement.refresh_token))
        rows = list(await session.scalars(select(RefreshToken)))
        assert all(row.revoked for row in rows)
        assert all(
            row.token_hash not in {pair.refresh_token, replacement.refresh_token} for row in rows
        )
        audits = list(await session.scalars(select(AuditLog)))
        assert {"auth.login", "auth.refresh", "auth.logout"} <= {
            row.payload["action"] for row in audits
        }


async def test_org_role_team_validation_disabled_users_and_bootstrap(identity_db):
    world = identity_db
    async with world.database.transaction() as session:
        org = world.org(session)
        with pytest.raises(AppError):
            await org.create_team(TeamWrite(name="forbidden"), actor=world.leader)
        with pytest.raises(AppError):
            await org.create_user(
                UserWrite(username="no-team", password=SecretStr("long-password"), role="member"),
                actor=world.admin,
            )
        with pytest.raises(AppError):
            await org.bootstrap_admin("another", SecretStr("long-password"))
        with pytest.raises(AppError):
            await org.update_user(
                world.admin.id, UserUpdate(role="member", team_id=world.team), actor=world.admin
            )
        with pytest.raises(AppError):
            await org.update_team(
                world.team, TeamWrite(name="sales", status="disabled"), actor=world.admin
            )
        await org.update_user(
            world.member.id,
            UserUpdate(role="member", team_id=world.team, status="disabled"),
            actor=world.admin,
        )
        with pytest.raises(AppError):
            await world.auth(session).login(
                LoginRequest(username="member", password=SecretStr("member-password"))
            )
        with pytest.raises(AppError):
            await org.identity(world.member.id)


async def test_refresh_expiry_and_concurrent_replay(identity_db):
    world = identity_db
    async with world.database.transaction() as session:
        pair = await world.auth(session).login(
            LoginRequest(username="member", password=SecretStr("member-password"))
        )
        row = await session.scalar(select(RefreshToken))
        row.expires_at = datetime(2000, 1, 1, tzinfo=UTC)
    async with world.database.transaction() as session:
        with pytest.raises(AppError):
            await world.auth(session).rotate(SecretStr(pair.refresh_token))


async def test_write_and_audit_rollback_together(identity_db):
    world = identity_db
    with pytest.raises(AppError):
        async with world.database.transaction() as session:
            await world.org(session).create_team(TeamWrite(name="rollback"), actor=world.admin)
            raise AppError(ErrorCode.CONFLICT)
    from app.modules.org.models import Team

    async with world.database.session() as session:
        assert await session.scalar(select(Team).where(Team.name == "rollback")) is None
        assert not [
            r
            for r in await session.scalars(select(AuditLog))
            if r.payload["target_id"] == "3" and r.payload["target_type"] == "team"
        ]
