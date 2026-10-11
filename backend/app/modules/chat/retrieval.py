"""Business-service adapter for the query pipeline's retrieval protocol."""

import json
import re
from typing import Any

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.llm import CallContext, RerankClient
from app.core.permission import VisibleScope
from app.core.prompt import PromptId
from app.modules.chat.ranking import Doc, DocKey, fuse, join_paths, keyword_scores, ranked
from app.modules.chat.schemas import ContextItem, RetrievalBundle
from app.modules.embedding.service import EmbeddingService
from app.modules.feedback.service import FeedbackService
from app.modules.metric.service import MetricService, render_template
from app.modules.schema.service import SchemaService
from app.modules.semantic.service import SemanticService

# One table or column description in the prompt; longer comments are cut.
NOTE_LIMIT = 200
# Retrieved context limits. Tables with at most WIDE_TABLE columns keep all of them.
MAX_TABLES = 6
MAX_METRICS = 3
MAX_EXAMPLES = 3
WIDE_TABLE = 20
# Items (terms, metrics, examples): keyword hits below this share of the best are noise.
KEYWORD_KEEP = 0.3
# Tables and columns: an absolute floor instead, the share of the question's weighted words
# a document must cover. Entity words many tables share (客户) score low but still count.
SCHEMA_FLOOR = 0.1
# Vector neighbours fused per group, and how many fused documents the reranker sees.
VECTOR_SCHEMA, VECTOR_ITEMS, RERANK_TOP = 10, 6, 30
_TIME_TYPES = ("date", "time")


class _NoTable:
    columns: list[Any] = []


_NO_TABLE = _NoTable()


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
        follow_up: str = "",
    ) -> RetrievalBundle:
        tables = await self._tables(user, scope.datasource_id)
        items: list[ContextItem] = []
        docs: list[Doc] = []
        # Aliases defined on a table or column make it findable by business words.
        aliases: dict[tuple[str, str], list[str]] = {}
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
            aliases.setdefault((target.table, target.column or ""), []).extend(term.aliases)
            labels = " ".join(str(value) for value in (term.enum_mapping or {}).values())
            docs.append(
                Doc(
                    "semantic",
                    term.id,
                    names=tuple(term.aliases),
                    text=f"{term.description or ''} {labels}",
                    tables={target.table: columns},
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
            docs.append(
                Doc(
                    "metric",
                    metric.id,
                    names=(metric.name, *metric.aliases),
                    text=metric.description or "",
                    tables=dict(checked.columns),
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
            docs.append(
                Doc("example", example.id, names=(), text=example.question, tables=checked.columns)
            )
        if mode == "hybrid":
            # A follow-up ("华东呢") is searched together with the question it continues.
            query = f"{question} {follow_up}".strip()
            picked = await self._pick(
                user, scope, query, tables, items, docs, aliases, pinned_metric_ids
            )
            if picked is not None:
                return picked
        # Full context: every visible table and every item.
        selected = {table.qualified_name for table in tables}
        selected.update(name for item in items for name in item.tables)
        return RetrievalBundle(
            tables={
                table.qualified_name: {column.name: column.data_type for column in table.columns}
                for table in tables
                if table.qualified_name in selected
            },
            items=items,
            mode="full",
        )

    async def _pick(
        self,
        user: CurrentUser,
        scope: VisibleScope,
        question: str,
        tables: list[Any],
        items: list[ContextItem],
        docs: list[Doc],
        aliases: dict[tuple[str, str], list[str]],
        pinned_metric_ids: frozenset[int],
    ) -> RetrievalBundle | None:
        """Only what this question needs; None when nothing matched (use full context)."""
        visible = {t.qualified_name: t for t in tables if t.qualified_name in scope.tables}
        for name, table in visible.items():
            docs.append(
                Doc(
                    "table",
                    table.id,
                    names=(table.name, name, *aliases.get((name, ""), [])),
                    text=f"{table.comment} {table.description}",
                    table=name,
                )
            )
            for column in table.columns:
                docs.append(
                    Doc(
                        "column",
                        column.id,
                        names=(column.name, *aliases.get((name, column.name), [])),
                        text=f"{column.comment} {column.description}",
                        table=name,
                        column=column.name,
                    )
                )
        by_key = {doc.key: doc for doc in docs}
        scores = keyword_scores(question, docs)
        schema_kinds = ("table", "column")
        keyword_schema = ranked(
            {key: v for key, v in scores.items() if key[0] in schema_kinds and v >= SCHEMA_FLOOR}
        )
        keyword_items = ranked(
            {key: v for key, v in scores.items() if key[0] not in schema_kinds}, KEYWORD_KEEP
        )
        nearest = await self._nearest(user, scope, question, list(by_key))
        vector_schema = [key for key in nearest if key[0] in schema_kinds][:VECTOR_SCHEMA]
        vector_items = [key for key in nearest if key[0] not in schema_kinds][:VECTOR_ITEMS]
        fused = fuse([keyword_schema, vector_schema, keyword_items, vector_items])
        order = sorted(fused, key=lambda key: (-fused[key], key))
        order = await self._reranked(user, question, order, by_key)
        if not order and not pinned_metric_ids:
            return None
        chosen: dict[str, set[str]] = {}
        for key in order:
            doc = by_key[key]
            if doc.kind in schema_kinds and (doc.table in chosen or len(chosen) < MAX_TABLES):
                chosen.setdefault(doc.table, set())
                if doc.column:
                    chosen[doc.table].add(doc.column)
        ranks = {key: index for index, key in enumerate(order)}

        def wanted(item: ContextItem) -> bool:
            return item.ref_id in pinned_metric_ids or bool(item.matched)

        # Pinned and literally named metrics first, then the best ranked ones.
        metrics = sorted(
            (
                item
                for item in items
                if item.kind == "metric" and (wanted(item) or (item.kind, item.ref_id) in ranks)
            ),
            key=lambda item: (not wanted(item), ranks.get(("metric", item.ref_id or 0), 0)),
        )
        examples = sorted(
            (
                item
                for item in items
                if item.kind == "example" and (item.kind, item.ref_id) in ranks
            ),
            key=lambda item: ranks[("example", item.ref_id or 0)],
        )
        kept = metrics[: max(MAX_METRICS, sum(map(wanted, metrics)))] + examples[:MAX_EXAMPLES]
        for item in kept:
            for name, columns in item.tables.items():
                chosen.setdefault(name, set()).update(columns)
        if not chosen:
            return None
        # Definitions of the chosen tables' columns (valid statuses, enum labels) always come.
        kept += [
            item
            for item in items
            if item.kind in ("semantic", "enum") and set(item.tables) <= set(chosen)
        ]
        for item in kept:
            if item.kind in ("semantic", "enum"):
                for name, columns in item.tables.items():
                    if len(visible.get(name, _NO_TABLE).columns) > WIDE_TABLE:
                        chosen[name].update(columns)
        return RetrievalBundle(
            tables={
                name: self._columns(visible[name], columns)
                for name, columns in chosen.items()
                if name in visible
            },
            items=kept,
            mode="hybrid",
        )

    @staticmethod
    def _columns(table: Any, wanted: set[str]) -> dict[str, str]:
        """All columns of a normal table; for a wide one, keys, times and wanted columns."""
        if len(table.columns) <= WIDE_TABLE:
            return {column.name: column.data_type for column in table.columns}
        return {
            column.name: column.data_type
            for column in table.columns
            if column.name in wanted
            or column.is_pk
            or column.name.endswith("_id")
            or any(kind in column.data_type.casefold() for kind in _TIME_TYPES)
        }

    async def _nearest(
        self, user: CurrentUser, scope: VisibleScope, question: str, keys: list[DocKey]
    ) -> list[DocKey]:
        """Vector neighbours among the given (already visible) documents; [] without a model."""
        if self.embedding is None or not keys:
            return []
        try:
            return await self.embedding.nearest(scope.datasource_id, question, keys, user)
        except AppError as error:
            if error.code != ErrorCode.LLM_UNAVAILABLE:
                raise
            return []

    async def _reranked(
        self, user: CurrentUser, question: str, order: list[DocKey], docs: dict[DocKey, Doc]
    ) -> list[DocKey]:
        """The rerank model's order for the top candidates, when one is configured."""
        if self.rerank is None or len(order) < 2:
            return order
        try:
            await self.rerank.registry.select(user.team_id, "rerank")
        except AppError as error:
            if error.code != ErrorCode.LLM_UNAVAILABLE:
                raise
            return order
        top = order[:RERANK_TOP]
        texts = [" ".join((*docs[key].names, docs[key].text)).strip() or key[0] for key in top]
        try:
            ranking = await self.rerank.rerank(
                question,
                texts,
                CallContext(
                    user_id=user.id,
                    team_id=user.team_id,
                    prompt_version=f"{PromptId.SQL.value}:rerank",
                ),
            )
        except AppError as error:
            if error.code != ErrorCode.LLM_UNAVAILABLE:
                raise
            return order
        reordered = [top[index] for index in ranking]
        return reordered + [key for key in top if key not in reordered] + order[RERANK_TOP:]

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
        """Add the tables that join the selected ones (both FK endpoints are authorized)."""
        tables = await self._tables(user, scope.datasource_id)
        endpoints = {
            column.id: table.qualified_name for table in tables for column in table.columns
        }
        edges = [
            (endpoints[relation.from_column_id], endpoints[relation.to_column_id])
            for relation in await self.schema.relations(
                scope.datasource_id, self.semantic.permissions, actor=user
            )
            if relation.from_column_id in endpoints and relation.to_column_id in endpoints
        ]
        by_name = {table.qualified_name: table for table in tables}
        for name in join_paths(set(bundle.tables), edges):
            if name in by_name and name in scope.tables:
                bundle.tables[name] = self._columns(by_name[name], set())
        return bundle
