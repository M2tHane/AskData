"""Chart, explanation and the streamed answer for an executed result."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from pydantic import ValidationError

from app.core.errors import AppError, ErrorCode
from app.core.prompt import PromptId
from app.core.sql.executor import QueryResult
from app.core.stream import EventType
from app.modules.chat.chart_heuristics import suggest_chart, with_series
from app.modules.chat.context_builder import SCOPE_LABEL
from app.modules.chat.dependencies import PipelineDependencies
from app.modules.chat.query_context import PREVIEW_ROWS, QueryContext, plain_rows, plain_value
from app.modules.chat.schemas import AnswerMeta, ChartSpec, ContextItem, UsedDefinition

# Rows the answer model reads to write its conclusion (already masked).
ANSWER_ROWS = 30
# How many first and last rows the answer sees of a longer result, besides column stats.
ANSWER_HEAD_ROWS = 15
ANSWER_TAIL_ROWS = 15


def answer_rows(result: QueryResult) -> dict[str, Any]:
    """What the answer may read of a result: all rows when short; otherwise the first and
    last rows plus per-column stats over every returned row, so "latest", totals and
    extremes come from the whole result rather than from its first page."""
    rows = result.rows
    if len(rows) <= ANSWER_ROWS:
        return {"rows": plain_rows(rows)}
    stats: dict[str, dict[str, Any]] = {}
    for column in result.columns:
        values = [row[column] for row in rows if row.get(column) is not None]
        numbers = [
            v for v in values if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)
        ]
        if numbers and len(numbers) == len(values):
            try:
                total: Any = sum(numbers)
            except TypeError:  # Decimal and float mixed in one column
                total = sum(float(v) for v in numbers)
            stats[column] = {
                "min": plain_value(min(numbers)),
                "max": plain_value(max(numbers)),
                "sum": plain_value(total),
                "count": len(numbers),
            }
        elif values and all(isinstance(v, (date, datetime)) for v in values):
            # Dates and datetimes do not compare with each other; their ISO text does.
            stamps = sorted(cast(list[date], values), key=lambda v: v.isoformat())
            stats[column] = {"min": plain_value(stamps[0]), "max": plain_value(stamps[-1])}
    return {
        "rows": plain_rows(rows[:ANSWER_HEAD_ROWS]),
        "tail_rows": plain_rows(rows[-ANSWER_TAIL_ROWS:]),
        "omitted_rows": len(rows) - ANSWER_HEAD_ROWS - ANSWER_TAIL_ROWS,
        "column_stats": stats,
    }


def chart_from_hint(
    hint: dict[str, Any] | None, columns: list[str], rows: list[dict[str, Any]]
) -> tuple[ChartSpec, bool]:
    """Validate the SQL call's chart proposal against the real result; fall back by shape.

    Returns the chart and whether the fallback was used. Labels and formats proposed by the
    model are kept for known columns even when its axes are unusable.
    """
    known = set(columns)
    fields = set(ChartSpec.model_fields)
    candidate = {key: value for key, value in (hint or {}).items() if key in fields}
    labels: dict[str, str] = {}
    formats: dict[str, Any] = {}
    if isinstance(candidate.get("labels"), dict):
        labels = {
            str(key): " ".join(str(value).split())[:40]
            for key, value in candidate["labels"].items()
            if key in known and str(value).strip()
        }
    if isinstance(candidate.get("formats"), dict):
        formats = {
            str(key): value
            for key, value in candidate["formats"].items()
            if key in known and value in {"number", "integer", "percent", "currency"}
        }
    candidate.update(labels=labels, formats=formats)
    try:
        chart = ChartSpec.model_validate(candidate)
    except ValidationError:
        chart = None
    if chart is not None and (
        (chart.x is not None and chart.x not in known)
        or any(column not in known for column in chart.y)
        or (chart.series is not None and chart.series not in known)
        or (chart.type != "table" and (chart.x is None or not chart.y))
    ):
        chart = None
    if chart is None:
        fallback = suggest_chart(columns, rows, title=str(candidate.get("title") or "")[:200])
        return fallback.model_copy(update={"labels": labels, "formats": formats}), True
    return chart.model_copy(update={"y_right": [c for c in chart.y_right if c in chart.y]}), False


def used_definitions(ctx: QueryContext) -> list[UsedDefinition]:
    """Only the semantic terms and metrics this answer relied on, not every retrieved item."""
    columns = ctx.guarded_sql.columns if ctx.guarded_sql else {}

    def used(item: ContextItem) -> bool:
        if item.kind == "example":
            return False
        if item.key in ctx.retrieval.pinned or item.score > 0:
            return True
        if ctx.clarification_choice and ctx.clarification_choice in {item.name}:
            return True
        # A column-level semantic term counts when its column appears in the checked SQL.
        return item.kind != "metric" and any(
            len(item_columns) == 1 and item_columns <= set(columns.get(table, ()))
            for table, item_columns in item.tables.items()
        )

    return [
        UsedDefinition(
            kind=item.kind,  # type: ignore[arg-type]
            name=item.name,
            scope=item.scope.value,
            version=item.version,
            # Plain-text definitions double as descriptions; JSON definitions never show.
            description=(
                item.description
                or ("" if item.definition.lstrip().startswith(("{", "[")) else item.definition)
            )[:300],
        )
        for item in ctx.retrieval.items
        if used(item)
    ][:12]


class PresentationStage:
    """Chart from the SQL call's hint, structured explanation, then the streamed answer."""

    name = "presentation"

    def __init__(self, deps: PipelineDependencies) -> None:
        self.deps = deps

    async def run(self, ctx: QueryContext) -> QueryContext:
        if ctx.result is None:
            raise AppError(ErrorCode.INTERNAL_ERROR)
        result = ctx.result
        preview = plain_rows(result.rows[:PREVIEW_ROWS])
        ctx.chart, fallback = chart_from_hint(ctx.chart_hint, result.columns, preview)
        if fallback:
            ctx.warnings.append("chart_fallback")
        ctx.chart = with_series(ctx.chart, result.columns, result.rows)
        notes = []
        if not result.rows:
            notes.append("结果为空，可能是条件过严或口径不符。")
        if result.truncated:
            notes.append(f"结果达到 {result.row_count:,} 行上限，已截断。")
        ctx.meta = AnswerMeta(
            interpretation=ctx.interpretation,
            filters=ctx.filters,
            definitions=used_definitions(ctx),
            time_ranges={
                key: value for key, value in ctx.time_ranges.items() if isinstance(value, dict)
            },
            notes=notes,
        )
        await ctx.event(
            EventType.DATA,
            {
                "columns": result.columns,
                "rows": preview,
                "row_count": result.row_count,
                "truncated": result.truncated,
                "preview_truncated": len(result.rows) > PREVIEW_ROWS,
            },
        )
        await ctx.event(EventType.CHART, ctx.chart.model_dump())
        await ctx.event(EventType.EXPLANATION, {"meta": ctx.meta.model_dump()})
        ctx.explanation = await self.answer(ctx, preview)
        return ctx

    async def answer(self, ctx: QueryContext, preview: list[dict[str, Any]]) -> str:
        result = ctx.result
        if result is None or not result.rows:
            # Nothing to summarize: a fixed sentence, no model call.
            text = "没有查到符合条件的数据。"
            await ctx.event(EventType.ANSWER, {"delta": text})
            return text
        data = {
            "question": ctx.business_question,
            "interpretation": ctx.interpretation,
            "filters": ctx.filters,
            "labels": ctx.chart.labels if ctx.chart else {},
            "columns": result.columns,
            **answer_rows(result),
            "row_count": result.row_count,
            "truncated": result.truncated,
            "time_ranges": ctx.meta.time_ranges if ctx.meta else {},
            "assumption": ctx.assumption,
            "definition_levels": {
                name: SCOPE_LABEL.get(level, level).removesuffix("级") + "口径"
                for name, level in ctx.retrieval.definition_levels.items()
            },
        }
        try:
            text = (
                await self.deps.stream_call(
                    ctx, PromptId.ANSWER, data, EventType.ANSWER, show_reasoning=False
                )
            ).strip()
        except AppError as error:
            if error.code != ErrorCode.LLM_UNAVAILABLE:
                raise AppError(error.code, http_status=error.http_status) from None
            text = ""
        if not text:
            ctx.warnings.append("answer_fallback")
            text = f"共 {result.row_count:,} 行结果。"
            await ctx.event(EventType.ANSWER, {"delta": text})
        return text[:3000]
