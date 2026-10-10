import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError, ErrorCode
from app.core.tasks import TaskManager
from app.modules.permission.schemas import TeamGrantsWrite
from app.modules.schema.service import SYNC_TASK, sync_handler
from tests.a_group.test_datasource_schema_permission import draft, snapshot
from tests.fakes import MemoryQueue, MemoryTaskStore


async def test_a_group_http_auth_openapi_roles_grants_and_sync(identity_db):
    from app.main import create_app

    world = identity_db
    application = create_app(world.settings)
    world.inspector.snapshot = snapshot("id", "phone", "secret")
    with TestClient(application, raise_server_exceptions=False) as client:
        application.state.datasource_inspector = world.inspector
        application.state.scope_cache = world.cache
        if getattr(application.state, "composition", None) is not None:
            application.state.composition.inspector = world.inspector
            application.state.composition.cache = world.cache
        application.state.task_manager = TaskManager(
            MemoryTaskStore(),
            MemoryQueue(),
            {
                SYNC_TASK: sync_handler(
                    application.state.database, world.box, world.cache, world.inspector
                )
            },
        )
        spec = client.get("/openapi.json").json()
        assert {
            "/api/auth/login",
            "/api/org/users",
            "/api/datasources",
            "/api/models",
            "/api/schema/{datasource_id}/sync",
            "/api/permissions/{datasource_id}/scope",
        } <= set(spec["paths"])
        assert client.get("/api/org/users").status_code == 401
        invalid = client.post("/api/auth/login", json={"username": "member", "password": "bad"})
        assert invalid.status_code == 401 and "bad" not in invalid.text
        login = client.post(
            "/api/auth/login", json={"username": "admin", "password": "administrator-password"}
        )
        assert login.status_code == 200, login.text
        pair = login.json()["data"]
        headers = {"Authorization": f"Bearer {pair['access_token']}"}
        assert client.get("/api/auth/me", headers=headers).json()["data"]["role"] == "admin"
        response = client.post(
            "/api/datasources",
            headers=headers,
            json=draft().model_dump(mode="json") | {"password": "datasource-password"},
        )
        assert response.status_code == 200, response.text
        assert "datasource-password" not in response.text
        source_id = response.json()["data"]["id"]
        queued = client.post(f"/api/schema/{source_id}/sync", headers=headers)
        assert queued.status_code == 200, queued.text
        task_id = queued.json()["data"]["id"]
        # Queue delivery is covered separately; here run the handler through its shared TaskManager.
        client.portal.call(application.state.task_manager.run, task_id)
        tables = client.get(f"/api/schema/{source_id}/tables", headers=headers).json()["data"]
        assert {c["name"] for c in tables[0]["columns"]} == {"id", "phone", "secret"}
        table_id, column_id = tables[0]["id"], tables[0]["columns"][0]["id"]
        response = client.put(
            f"/api/permissions/{source_id}/teams/{world.team}",
            headers=headers,
            json={"tables": [{"table_id": table_id, "columns": [{"column_id": column_id}]}]},
        )
        assert response.status_code == 200, response.text
        member_pair = client.post(
            "/api/auth/login", json={"username": "member", "password": "member-password"}
        ).json()["data"]
        member_headers = {"Authorization": f"Bearer {member_pair['access_token']}"}
        assert client.get("/api/org/users", headers=member_headers).status_code == 403
        assert not client.get(f"/api/schema/{source_id}/tables", headers=member_headers).json()[
            "data"
        ]
        response = client.put(
            f"/api/permissions/{source_id}/teams/{world.team}/members",
            headers=headers,
            json={"tables": [{"table_id": table_id, "column_ids": [column_id]}]},
        )
        assert response.status_code == 200, response.text
        response = client.get(f"/api/permissions/{source_id}/scope", headers=member_headers)
        assert response.json()["data"]["tables"]["public.orders"]["columns"] == ["id"]
        assert "secret" not in response.text and "all_columns" not in response.text
        disabled = client.patch(
            f"/api/datasources/{source_id}/status", headers=headers, json={"status": "disabled"}
        )
        assert disabled.status_code == 200
        response = client.get(f"/api/permissions/{source_id}/scope", headers=member_headers)
        assert response.status_code == 503
        # Disabling stops queries, not management: the admin still sees fields and edits scope.
        tables = client.get(f"/api/schema/{source_id}/tables", headers=member_headers)
        assert tables.status_code == 503
        tables = client.get(f"/api/schema/{source_id}/tables", headers=headers)
        assert tables.status_code == 200 and len(tables.json()["data"][0]["columns"]) == 3
        visibility = client.get(f"/api/permissions/{source_id}/visibility", headers=headers)
        assert visibility.status_code == 200, visibility.text
        response = client.put(
            f"/api/permissions/{source_id}/visibility",
            headers=headers,
            json={"team_ids": [world.team]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["team_ids"] == [world.team]
        assert client.get("/api/schema/999999/tables", headers=headers).status_code == 404
        replay = client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})
        assert replay.status_code == 200, replay.text
        assert (
            client.post(
                "/api/auth/refresh", json={"refresh_token": pair["refresh_token"]}
            ).status_code
            == 401
        )


async def test_uncommitted_permission_snapshot_cannot_leak_after_rollback(identity_db):
    world = identity_db
    world.inspector.snapshot = snapshot("id")
    async with world.database.transaction() as session:
        source = await world.datasource(session).create(draft(), actor=world.admin)
        await world.schema(session).synchronize(source.id, world.grants(session), actor=world.admin)
        table = (await world.schema(session).catalog(source.id))[0]
    from app.modules.permission.schemas import ColumnGrantWrite, TableGrantWrite

    with pytest.raises(AppError):
        async with world.database.transaction() as session:
            grants = world.grants(session)
            await grants.replace(
                source.id,
                world.team,
                TeamGrantsWrite(
                    tables=[
                        TableGrantWrite(
                            table_id=table.id,
                            member_visible=True,
                            columns=[
                                ColumnGrantWrite(column_id=table.columns[0].id, member_visible=True)
                            ],
                        )
                    ]
                ),
                actor=world.admin,
            )
            assert (await grants.visible_scope(world.member, source.id)).tables
            assert not world.cache.values
            raise AppError(ErrorCode.CONFLICT)
    async with world.database.session() as session:
        assert not (await world.grants(session).visible_scope(world.member, source.id)).tables
