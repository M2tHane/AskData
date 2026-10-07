import httpx
from fastapi import FastAPI

from app.core.deps import current_user
from app.main import create_app
from app.modules.audit import api as audit
from app.modules.embedding import api as embedding
from app.modules.feedback import api as feedback
from app.modules.metric import api as metric
from app.modules.promotion import api as promotion
from app.modules.querylog import api as querylog
from app.modules.semantic import api as semantic
from app.modules.usage import api as usage

APIS = [semantic, metric, promotion, feedback, querylog, usage, audit, embedding]


def test_all_routers_generate_openapi_envelopes():
    app = FastAPI()
    for api in APIS:
        app.include_router(api.router, prefix="/api")
    document = app.openapi()
    expected = {
        "/api/semantic": {"get", "post"},
        "/api/semantic/{identifier}": {"get", "put", "delete"},
        "/api/metric": {"get", "post"},
        "/api/metric/{identifier}": {"get", "put", "delete"},
        "/api/promotion": {"get", "post"},
        "/api/promotion/{identifier}/review": {"post"},
        "/api/feedback": {"get", "post"},
        "/api/feedback/{identifier}/handle": {"post"},
        "/api/feedback/{identifier}/convert": {"post"},
        "/api/feedback/examples/list": {"get"},
        "/api/feedback/examples": {"post"},
        "/api/feedback/examples/{identifier}/review": {"post"},
        "/api/querylog": {"get"},
        "/api/querylog/{identifier}": {"get"},
        "/api/usage": {"get"},
        "/api/usage/quota/{team_id}": {"get", "put"},
        "/api/audit": {"get"},
        "/api/embedding/update": {"post"},
        "/api/embedding/rebuild": {"post"},
        "/api/embedding/search": {"post"},
    }
    assert {path: set(operations) for path, operations in document["paths"].items()} == expected
    for path, operations in document["paths"].items():
        for operation in operations.values():
            ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
            assert "Envelope" in ref, path


async def test_semantic_http_validation_auth_and_write_audit(b_services, member):
    app = create_app()
    app.include_router(semantic.router, prefix="/api")
    app.dependency_overrides[semantic.get_service] = lambda: b_services.semantic
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/semantic?datasource_id=1")).status_code == 401
        app.dependency_overrides[current_user] = lambda: member
        response = await client.post(
            "/api/semantic", json={"datasource_id": 1, "target_type": "column", "target_id": 1}
        )
        assert response.status_code == 200 and response.json()["code"] == "OK"
        identifier = response.json()["data"]["id"]
        assert (await client.get(f"/api/semantic/{identifier}")).json()["data"]["id"] == identifier
        invalid = await client.post(
            "/api/semantic", json={"datasource_id": 1, "target_type": "column", "target_id": 0}
        )
        assert invalid.status_code == 422 and invalid.json()["code"] == "VALIDATION_ERROR"
