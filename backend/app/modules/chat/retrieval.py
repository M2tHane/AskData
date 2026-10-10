"""Business-service adapter for the query pipeline's retrieval protocol."""

import json
import re
from typing import Any

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

# One table or column description in the prompt; longer comments are cut.
NOTE_LIMIT = 200


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
        # Request-scoped instance: browse the authorized schema once per question.
        self._browsed: dict[tuple[int, int], Any] = {}

    async def _tables(self, user: CurrentUser, datasource_id: int) -> Any:
        key = (user.id, datasource_id)
        if key not in self._browsed:
            self._browsed[key] = await self.schema.browse(
                datasource_id, self.semantic.permissions, actor=user
            )
        return self._browsed[key]

    async def retrieve(
        self,
        user: CurrentUser,
        scope: VisibleScope,
        question: str,
        mode: str,
        pinned_metric_ids: frozenset[int] = frozenset(),
    ) -> RetrievalBundle:
        tables = await self._tables(user, scope.datasource_id)
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
                    description=term.description or "",
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
                    description=metric.description or "",
                    tables=checked.columns,
                    score=self._score(question, [metric.name, *metric.aliases]),
                    matched=self._matched(question, [metric.name, *metric.aliases]),
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
                or (item.kind == "metric" and item.ref_id in pinned_metric_ids)
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
    def _matched(question: str, names: list[str]) -> str:
        found = [name for name in names if name and name.casefold() in question.casefold()]
        return max(found, key=len, default="")

    @staticmethod
    def _score(question: str, names: list[str]) -> float:
        return (
            1.0 if any(name and name.casefold() in question.casefold() for name in names) else 0.0
        )

    async def notes(
        self, user: CurrentUser, scope: VisibleScope, tables: list[str]
    ) -> dict[str, str]:
        """Descriptions of the given visible tables and their visible columns.

        A description that names a hidden table or column is left out, so comments written for
        the whole schema cannot reveal what this user may not see.
        """
        visible = {
            table.qualified_name: table for table in await self._tables(user, scope.datasource_id)
        }
        catalog = await self.schema.catalog(scope.datasource_id)
        seen = {
            name.casefold()
            for table in visible.values()
            for name in (table.name, *(c.name for c in table.columns))
        }
        hidden: set[str] = set()
        for table in catalog:
            shown = visible.get(table.qualified_name)
            if shown is None:
                hidden.update({table.name.casefold(), table.qualified_name.casefold()})
            names = {c.name for c in shown.columns} if shown else set()
            hidden.update(c.name.casefold() for c in table.columns if c.name not in names)
        hidden -= seen
        # ASCII boundaries: Chinese text runs straight into a name ("cost为采购成本").
        leaks = (
            re.compile(
                "|".join(
                    rf"(?<![A-Za-z0-9_.]){re.escape(name)}(?![A-Za-z0-9_])"
                    for name in sorted(hidden)
                ),
                re.IGNORECASE,
            )
            if hidden
            else None
        )

        def keep(text: str) -> str | None:
            text = " ".join(text.split())[:NOTE_LIMIT]
            return text if text and not (leaks and leaks.search(text)) else None

        notes: dict[str, str] = {}
        for name in tables:
            described = visible.get(name)
            if described is None:
                continue
            if note := keep(described.description or described.comment):
                notes[name] = note
            for column in described.columns:
                if note := keep(column.description or column.comment):
                    notes[f"{name}.{column.name}"] = note
        return notes

    async def expand_relations(
        self, user: CurrentUser, scope: VisibleScope, bundle: RetrievalBundle
    ) -> RetrievalBundle:
        tables = await self._tables(user, scope.datasource_id)
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
