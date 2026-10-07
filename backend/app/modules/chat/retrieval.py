"""Business-service adapter for the query pipeline's retrieval protocol."""

import json

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.llm import CallContext, RerankClient
from app.core.permission import VisibleScope
from app.core.prompt import PromptId
from app.modules.chat.schemas import ContextItem, RetrievalBundle
from app.modules.embedding.service import EmbeddingService, SearchInput
from app.modules.feedback.service import FeedbackService
from app.modules.metric.service import MetricService, render_template
from app.modules.schema.service import SchemaService
from app.modules.semantic.service import SemanticService


class ServiceRetrievalIndex:
    def __init__(
        self,
        schema: SchemaService,
        semantic: SemanticService,
        metric: MetricService,
        feedback: FeedbackService,
        embedding: EmbeddingService | None = None,
        rerank: RerankClient | None = None,
    ) -> None:
        self.schema, self.semantic, self.metric, self.feedback = schema, semantic, metric, feedback
        self.embedding, self.rerank = embedding, rerank

    async def retrieve(
        self, user: CurrentUser, scope: VisibleScope, question: str, mode: str
    ) -> RetrievalBundle:
        tables = await self.schema.browse(
            scope.datasource_id, self.semantic.permissions, actor=user
        )
        items: list[ContextItem] = []
        for term in await self.semantic.list(user, scope.datasource_id):
            target = await self.semantic.targets.resolve_target(
                term.datasource_id, term.target_type, term.target_id
            )
            visible = scope.tables.get(target.table)
            if visible is None:
                continue
            columns = frozenset({target.column}) if target.column else visible.columns
            items.append(
                ContextItem(
                    **term.model_dump(
                        include={"scope", "status", "owner_team_id", "owner_user_id"}
                    ),
                    kind="enum" if term.target_type == "enum" else "semantic",
                    ref_id=term.id,
                    key=f"semantic:{term.target_type}:{term.target_id}",
                    name=term.aliases[0]
                    if term.aliases
                    else f"{target.table}.{target.column or '*'}",
                    version=term.version,
                    definition=json.dumps(
                        {
                            "aliases": term.aliases,
                            "description": term.description,
                            "enum_mapping": term.enum_mapping,
                        },
                        ensure_ascii=False,
                    ),
                    tables={target.table: columns},
                    score=self._score(question, term.aliases),
                )
            )
        for metric in await self.metric.list(user, scope.datasource_id):
            datasource = await self.metric.datasources.resolve(metric.datasource_id, user)
            checked = self.metric.guard.check(
                render_template(metric.sql_template, metric.params), datasource.dialect, scope
            )
            items.append(
                ContextItem(
                    **metric.model_dump(
                        include={"scope", "status", "owner_team_id", "owner_user_id"}
                    ),
                    kind="metric",
                    ref_id=metric.id,
                    key=f"metric:{metric.name.casefold()}",
                    name=metric.name,
                    version=metric.version,
                    definition=json.dumps(
                        {
                            "sql_template": metric.sql_template,
                            "params": metric.params,
                            "description": metric.description,
                        },
                        ensure_ascii=False,
                    ),
                    tables=checked.columns,
                    score=self._score(question, [metric.name, *metric.aliases]),
                )
            )
        for example in await self.feedback.examples(user, scope.datasource_id):
            datasource = await self.feedback.datasources.resolve(example.datasource_id, user)
            checked = self.feedback.guard.check(example.sql, datasource.dialect, scope)
            items.append(
                ContextItem(
                    **example.model_dump(
                        include={"scope", "status", "owner_team_id", "owner_user_id"}
                    ),
                    kind="example",
                    ref_id=example.id,
                    key=f"example:{example.id}",
                    name=example.question,
                    version=example.version,
                    definition=checked.normalized_sql,
                    tables=checked.columns,
                    score=self._score(question, [example.question]),
                )
            )
        selected = {table.qualified_name for table in tables}
        if mode == "hybrid":
            if self.embedding is None:
                raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)
            documents = await self.embedding.search(
                SearchInput(datasource_id=scope.datasource_id, query=question, limit=30), user
            )
            if self.rerank is not None and documents:
                try:
                    await self.rerank.registry.select(user.team_id, "rerank")
                except AppError as error:
                    if error.code != ErrorCode.LLM_UNAVAILABLE:
                        raise
                else:
                    order = await self.rerank.rerank(
                        question,
                        [document.text for document in documents],
                        CallContext(
                            user_id=user.id,
                            team_id=user.team_id,
                            prompt_version=f"{PromptId.SQL.value}:v1",
                        ),
                    )
                    documents = [documents[index] for index in order]
            selected = {name for document in documents for name in document.references}
            references = {(doc.kind, doc.ref_id) for doc in documents}
            items = [
                item
                for item in items
                if ("semantic" if item.kind == "enum" else item.kind, item.ref_id) in references
            ]
        selected.update(name for item in items for name in item.tables)
        return RetrievalBundle(
            tables={
                table.qualified_name: {column.name: column.data_type for column in table.columns}
                for table in tables
                if table.qualified_name in selected
            },
            items=items,
            mode="hybrid" if mode == "hybrid" else "full",
        )

    @staticmethod
    def _score(question: str, names: list[str]) -> float:
        return (
            1.0 if any(name and name.casefold() in question.casefold() for name in names) else 0.0
        )

    async def expand_relations(
        self, user: CurrentUser, scope: VisibleScope, bundle: RetrievalBundle
    ) -> RetrievalBundle:
        tables = await self.schema.browse(
            scope.datasource_id, self.semantic.permissions, actor=user
        )
        endpoints = {
            column.id: table.qualified_name for table in tables for column in table.columns
        }
        selected = set(bundle.tables)
        # One hop only; both FK endpoints come from the authorized schema service.
        for relation in await self.schema.relations(
            scope.datasource_id, self.semantic.permissions, actor=user
        ):
            source, target = (
                endpoints.get(relation.from_column_id),
                endpoints.get(relation.to_column_id),
            )
            if source and target and (source in selected or target in selected):
                bundle.tables[source] = {
                    column.name: column.data_type
                    for table in tables
                    if table.qualified_name == source
                    for column in table.columns
                }
                bundle.tables[target] = {
                    column.name: column.data_type
                    for table in tables
                    if table.qualified_name == target
                    for column in table.columns
                }
        return bundle
