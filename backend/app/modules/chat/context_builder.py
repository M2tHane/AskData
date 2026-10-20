"""What the model may see for one question: retrieved, authorized and disambiguated context."""

from typing import Any, Literal, Protocol

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.permission import TableScope, VisibleScope
from app.core.scope import ScopedQuery
from app.modules.chat.schemas import Clarification, ContextItem, RetrievalBundle

SCOPE_LABEL = {"platform": "平台级", "team": "团队级", "personal": "个人级"}


def _scoped_choice(item: ContextItem) -> str:
    """A clarification option a user can read: name, scope and the plain-language meaning,
    never the stored JSON definition."""
    label = SCOPE_LABEL.get(item.scope.value, item.scope.value)
    return (
        f"{item.name}（{label}）：{item.description}"
        if item.description
        else f"{item.name}（{label}）"
    )


class RetrievalIndex(Protocol):
    """B-group service adapter: pre-filter every kind by user and scope before retrieval.

    hybrid means keyword + embedding, optionally reranked. expand_relations must use
    only authorized foreign-key endpoints. No repository or B-group imports are needed.
    """

    async def retrieve(
        self,
        user: CurrentUser,
        scope: VisibleScope,
        question: str,
        mode: str,
        pinned_metric_ids: frozenset[int] = frozenset(),
    ) -> RetrievalBundle:
        """Hybrid indexes may also take follow_up=: earlier question text to search with."""
        ...

    async def expand_relations(
        self, user: CurrentUser, scope: VisibleScope, bundle: RetrievalBundle
    ) -> RetrievalBundle: ...


class ScopeOnlyIndex:
    """Safe bootstrap adapter; hybrid deployments must inject a real index service."""

    async def retrieve(
        self,
        user: CurrentUser,
        scope: VisibleScope,
        question: str,
        mode: str,
        pinned_metric_ids: frozenset[int] = frozenset(),
    ) -> RetrievalBundle:
        if mode == "hybrid":
            raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503)
        return RetrievalBundle(
            tables={
                name: {column: table.all_columns[column] for column in sorted(table.columns)}
                for name, table in scope.tables.items()
            }
        )

    async def expand_relations(
        self, user: CurrentUser, scope: VisibleScope, bundle: RetrievalBundle
    ) -> RetrievalBundle:
        return bundle


class ContextBuilder:
    def __init__(self, index: RetrievalIndex, threshold: int = 30, semantics: bool = True) -> None:
        self.index, self.threshold = index, threshold
        # Off only in ablation runs: no glossary terms, metrics or examples reach the model.
        self.semantics = semantics

    async def build(
        self,
        user: CurrentUser,
        scope: VisibleScope,
        question: str,
        choice: str | None = None,
        pinned_metric_ids: frozenset[int] = frozenset(),
        choice_ref: str | None = None,
        *,
        full: bool = False,
        follow_up: str = "",
        keep_tables: frozenset[str] = frozenset(),
    ) -> RetrievalBundle:
        """Context for one question.

        full forces every visible table and item (the fallback when retrieval missed).
        follow_up is earlier question text a follow-up depends on; keep_tables are the tables
        its inherited SQL used, kept whole so "按月拆开" still has the previous tables.
        """
        # Do not give the index withheld column metadata retained by the SQL guard.
        safe_scope = VisibleScope(
            datasource_id=scope.datasource_id,
            tables={
                name: TableScope(
                    all_columns={column: table.all_columns[column] for column in table.columns},
                    columns=table.columns,
                )
                for name, table in scope.tables.items()
            },
        )
        # An answered clarification names its option by label: give the model everything.
        mode: Literal["full", "hybrid"] = (
            "full" if full or choice or len(safe_scope.tables) <= self.threshold else "hybrid"
        )
        extra: dict[str, Any] = {}
        if pinned_metric_ids:
            extra["pinned_metric_ids"] = pinned_metric_ids
        if follow_up and mode == "hybrid":
            extra["follow_up"] = follow_up
        bundle = await self.index.retrieve(user, safe_scope, question, mode, **extra)
        if not self.semantics:
            bundle.items = []
        if bundle.mode == "hybrid":
            for name in keep_tables:
                if name in safe_scope.tables:
                    bundle.tables[name] = dict(safe_scope.tables[name].all_columns)
        bundle = await self.index.expand_relations(user, safe_scope, bundle)
        mode = bundle.mode if mode == "hybrid" else mode
        tables = {
            name: {
                column: scope.tables[name].all_columns[column]
                for column in columns
                if column in scope.tables[name].columns
            }
            for name, columns in bundle.tables.items()
            if name in scope.tables
        }
        if mode == "full":
            tables = {name: dict(table.all_columns) for name, table in safe_scope.tables.items()}
        authorized = [
            item
            for item in bundle.items
            if item.tables
            and all(
                name in scope.tables and columns <= scope.tables[name].columns
                for name, columns in item.tables.items()
            )
        ]
        visible_items = ScopedQuery.visible_to(authorized, user)
        merged = ScopedQuery.merge(visible_items, user, "key")
        ambiguities = list(bundle.ambiguities)
        # Same term defined differently at several levels: the user's own level wins
        # (personal > team > platform) without asking; the answer names the level used.
        effective = {item.key: item for item in merged}
        levels: dict[str, str] = {}
        for key in dict.fromkeys(item.key for item in visible_items):
            matches = [item for item in visible_items if item.key == key and item.score > 0]
            if len({item.definition for item in matches}) > 1 and key in effective:
                levels[effective[key].name] = effective[key].scope.value
        metrics = sorted(
            (item for item in merged if item.kind == "metric"),
            key=lambda item: item.score,
            reverse=True,
        )
        # Several metrics are ambiguous only when they answer to the same words. Metrics named
        # by different words are all meant: "销售额目标达成率" names 销售额 and 目标达成率.
        # A term inside a longer matched term (达成率 in 目标达成率) does not count on its own.
        hits = [item for item in metrics if item.score > 0]
        terms = {item.matched for item in hits if item.matched}
        hits = [
            item
            for item in hits
            if not any(
                item.matched and item.matched != term and item.matched in term for term in terms
            )
        ]
        groups: dict[str, list[ContextItem]] = {}
        for item in hits:
            groups.setdefault(item.matched.casefold(), []).append(item)
        for group in groups.values():
            if len(group) >= 2:
                ambiguities.append(
                    Clarification(question="请选择指标", choices=[item.name for item in group[:8]])
                )
                break
        if choice:
            metric_ref = (
                choice_ref.split(":", 1)[1].strip().casefold()
                if choice_ref and choice_ref.casefold().startswith("metric:")
                else None
            )
            selected = [
                item
                for item in visible_items
                # The raw-definition form is what clarifications offered before readable labels.
                if choice
                in {
                    item.name,
                    _scoped_choice(item),
                    f"{item.name}（{item.scope.value}级）：{item.definition}",
                }
                or (item.kind == "metric" and item.name.casefold() == metric_ref)
            ]
            if len(selected) == 1:
                chosen = selected[0]
                merged = [
                    item
                    for item in merged
                    if item.key != chosen.key and (chosen.kind != "metric" or item.kind != "metric")
                ]
                merged.append(chosen)
                if chosen.name in levels:
                    levels[chosen.name] = chosen.scope.value
        pinned_keys: list[str] = []
        if pinned_metric_ids:
            merged = [
                item.model_copy(update={"score": 2.0})
                if item.kind == "metric" and item.ref_id in pinned_metric_ids
                else item
                for item in merged
            ]
            pinned_keys = [
                item.key
                for item in merged
                if item.kind == "metric" and item.ref_id in pinned_metric_ids
            ]
            if pinned_keys:
                # An explicit @mention settles which metric is meant: no metric ambiguity remains.
                ambiguities = [a for a in ambiguities if a.question != "请选择指标"]
        # Indexes without schema descriptions (tests, bootstrap) simply give none.
        describe = getattr(self.index, "notes", None)
        notes = await describe(user, scope, list(tables)) if describe else {}
        # Descriptions only of what is sent: a pruned column's note would be noise.
        notes = {
            key: note
            for key, note in notes.items()
            if key in tables
            or (
                key.rsplit(".", 1)[0] in tables
                and key.rsplit(".", 1)[1] in tables[key.rsplit(".", 1)[0]]
            )
        }
        # Example SQL is still only prompt data; every resulting SQL passes the guard.
        return RetrievalBundle(
            tables=tables,
            notes=notes,
            items=merged,
            required_conditions=bundle.required_conditions,
            ambiguities=ambiguities,
            mode=mode,
            pinned=pinned_keys,
            definition_levels=levels,
        )
