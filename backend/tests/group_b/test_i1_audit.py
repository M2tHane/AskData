from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select

from app.core.audit import AuditLog
from app.core.permission import TableGrant
from app.core.scope import ScopeLevel
from app.main import create_app
from app.modules.chat.pipeline import ContextBuilder
from app.modules.chat.retrieval import ServiceRetrievalIndex
from app.modules.embedding.schemas import IndexDocument, IndexReference, SearchInput
from app.modules.feedback.schemas import ExampleInput, ExampleReview
from app.modules.metric.schemas import MetricInput
from app.modules.semantic.schemas import SemanticInput
from app.modules.usage.models import TeamQuota
from tests.group_b.test_embedding import service_for
from tests.group_b.test_usage import usage_service


@pytest.mark.parametrize(
    "path",
    [
        "/api/org/users",
        "/api/datasources",
        "/api/schema/1/tables",
        "/api/permissions/1/scope",
        "/api/models",
        "/api/semantic?datasource_id=1",
        "/api/metric?datasource_id=1",
        "/api/promotion",
        "/api/feedback",
        "/api/querylog",
        "/api/usage",
        "/api/audit",
    ],
)
async def test_protected_routes_authenticate_before_unconfigured_services(path):
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 401
    assert set(response.json()) == {"code", "message", "data"}
    assert response.json()["code"] == "AUTH_FAILED" and response.json()["data"] is None


async def test_full_retrieval_uses_live_services_versions_enums_and_approved_examples(
    b_services, member, admin
):
    platform = await b_services.semantic.save(
        SemanticInput(
            datasource_id=1,
            target_type="enum",
            target_id=1,
            aliases=["订单"],
            enum_mapping={"已支付": "1"},
            scope=ScopeLevel.PLATFORM,
        ),
        actor=admin,
    )
    personal = await b_services.semantic.save(
        SemanticInput(
            datasource_id=1,
            target_type="enum",
            target_id=1,
            aliases=["订单"],
            description="个人口径",
            scope=ScopeLevel.PERSONAL,
        ),
        actor=member,
    )
    metric = await b_services.metric.save(
        MetricInput(
            datasource_id=1,
            name="订单数",
            sql_template="SELECT COUNT(id) FROM orders",
            scope=ScopeLevel.PLATFORM,
        ),
        actor=admin,
    )
    example = await b_services.feedback.create_example(
        ExampleInput(
            datasource_id=1, question="订单", sql="SELECT id FROM orders", scope=ScopeLevel.PLATFORM
        ),
        actor=admin,
    )
    await b_services.feedback.review_example(example.id, ExampleReview(approved=True), actor=admin)
    await b_services.feedback.create_example(
        ExampleInput(
            datasource_id=1,
            question="unapproved",
            sql="SELECT id FROM orders",
            scope=ScopeLevel.PLATFORM,
        ),
        actor=admin,
    )

    class Schema:
        async def browse(self, datasource_id, permissions, *, actor):
            return [
                SimpleNamespace(
                    name="orders",
                    qualified_name="orders",
                    comment="",
                    description="",
                    columns=[
                        SimpleNamespace(
                            id=1, name="id", data_type="BIGINT", comment="", description=""
                        )
                    ],
                )
            ]

        async def catalog(self, datasource_id):
            return await self.browse(datasource_id, None, actor=None)

        async def relations(self, datasource_id, permissions, *, actor):
            return []

    index = ServiceRetrievalIndex(
        Schema(), b_services.semantic, b_services.metric, b_services.feedback
    )
    scope = await b_services.permissions.visible_scope(member, 1)
    bundle = await ContextBuilder(index).build(member, scope, "订单")
    enum = next(item for item in bundle.items if item.kind == "enum")
    assert enum.ref_id == personal.id and enum.version == personal.version
    assert enum.scope == ScopeLevel.PERSONAL
    # Conflicting levels resolve to the user's own without a clarification.
    assert not any("请选择指标或语义口径" == item.question for item in bundle.ambiguities)
    assert "personal" in bundle.definition_levels.values()
    assert next(item for item in bundle.items if item.kind == "metric").ref_id == metric.id
    assert [item.ref_id for item in bundle.items if item.kind == "example"] == [example.id]
    assert platform.id != personal.id and "unapproved" not in bundle.model_dump_json()
    b_services.provider.grants[0].member_columns = frozenset({"phone"})
    bundle = await ContextBuilder(index).build(
        member, await b_services.permissions.visible_scope(member, 1), "订单"
    )
    assert not bundle.items


async def test_fk_expansion_only_adds_authorized_endpoints(b_services, member):
    b_services.provider.grants.append(
        TableGrant(
            name="customers",
            all_columns={"id": "BIGINT"},
            team_visible=True,
            member_visible=True,
            team_columns={"id"},
            member_columns={"id"},
        )
    )

    class Schema:
        async def browse(self, datasource_id, permissions, *, actor):
            return [
                SimpleNamespace(
                    qualified_name=name,
                    columns=[SimpleNamespace(id=identifier, name="id", data_type="BIGINT")],
                )
                for name, identifier in [("orders", 1), ("customers", 2)]
            ]

        async def relations(self, datasource_id, permissions, *, actor):
            return [
                SimpleNamespace(from_column_id=1, to_column_id=2),
                SimpleNamespace(from_column_id=1, to_column_id=99),
            ]

    from app.modules.chat.schemas import RetrievalBundle

    index = ServiceRetrievalIndex(
        Schema(), b_services.semantic, b_services.metric, b_services.feedback
    )
    bundle = await index.expand_relations(
        member,
        await b_services.permissions.visible_scope(member, 1),
        RetrievalBundle(tables={"orders": {"id": "BIGINT"}}),
    )
    assert set(bundle.tables) == {"orders", "customers"}


async def test_embedding_filters_before_database_ranking_and_audits_search(b_services, member):
    service, _, source, _ = service_for(b_services)
    hidden = IndexDocument(
        kind="column",
        ref_id=2,
        datasource_id=1,
        text="private",
        references={"orders": {"phone"}},
        scope=ScopeLevel.PERSONAL,
        owner_user_id=99,
    )
    visible = source.document_value

    async def catalog(datasource_id):
        return [
            IndexReference(kind="column", ref_id=ref_id, datasource_id=1, actor_id=1)
            for ref_id in (2, 1)
        ]

    async def document(reference, actor):
        return hidden if reference.ref_id == 2 else visible

    allowed_refs = []

    async def candidates(*args):
        allowed_refs.extend(args[-1])
        return []

    source.catalog, source.document = catalog, document
    service.repository.candidates = candidates
    assert await service.search(SearchInput(datasource_id=1, query="Order"), member) == []
    assert allowed_refs == [("column", 1)]
    audits = await b_services.session.scalars(select(AuditLog))
    assert any(row.payload["action"] == "embedding.search" for row in audits)


async def test_get_quota_does_not_write_configuration(b_database, admin):
    async with b_database.transaction() as session:
        service = usage_service(b_database, session)
        quota = await service.get_quota(1, admin)
        assert quota.monthly_token_limit is None and quota.daily_query_limit is None
        assert await session.scalar(select(func.count()).select_from(TeamQuota)) == 0
