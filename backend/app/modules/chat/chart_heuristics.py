"""Deterministic chart fallback used when the model's chart configuration is unusable."""

import re
from datetime import date, datetime
from typing import Any

from app.modules.chat.schemas import ChartSpec

MAX_BAR_CATEGORIES = 30
MAX_SERIES = 5
MIN_SCATTER_POINTS = 5
_DATE_TEXT = re.compile(r"^\d{4}-\d{2}(-\d{2})?([ T].*)?$")


def _numeric(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        or (value is not None and type(value).__name__ == "Decimal")
    )


def _is_numeric_column(rows: list[dict[str, Any]], column: str) -> bool:
    values = [row.get(column) for row in rows[:20] if row.get(column) is not None]
    return bool(values) and all(_numeric(v) for v in values)


def _is_temporal_column(rows: list[dict[str, Any]], column: str) -> bool:
    values = [row.get(column) for row in rows[:20] if row.get(column) is not None]
    return bool(values) and all(
        isinstance(v, date | datetime) or (isinstance(v, str) and _DATE_TEXT.match(v))
        for v in values
    )


def with_series(chart: ChartSpec, columns: list[str], rows: list[dict[str, Any]]) -> ChartSpec:
    """Mark long-format results (x, split column, one measure) so each split value is a series.

    Only for a bar/line chart with one measure whose result has exactly one more text column
    holding 2..5 distinct values; the front end pivots the rows. Anything else is untouched.
    """
    if chart.type not in ("bar", "line") or chart.series or chart.x is None or len(chart.y) != 1:
        return chart
    split = [column for column in columns if column not in (chart.x, chart.y[0])]
    if len(columns) != 3 or len(split) != 1 or _is_numeric_column(rows, split[0]):
        return chart
    if not 2 <= len({row.get(split[0]) for row in rows}) <= MAX_SERIES:
        return chart
    return chart.model_copy(update={"series": split[0]})


def suggest_chart(columns: list[str], rows: list[dict[str, Any]], title: str = "") -> ChartSpec:
    """Pick a chart from the result shape only; never invents columns.

    - category + 1..5 numbers: line when the category is temporal, bar up to 30 rows, else table
    - text, text, number (long format): one series per distinct value of the second column
    - two numeric columns, at least 5 rows: scatter
    - one row with 2..5 number columns (a wide aggregate): bar over the column names,
      x stays empty and the front end labels each bar with its column name
    """
    if not rows or len(columns) < 2:
        return ChartSpec(type="table", title=title)
    first, rest = columns[0], columns[1:]
    if (
        2 <= len(columns) <= MAX_SERIES + 1
        and not _is_numeric_column(rows, first)
        and all(_is_numeric_column(rows, column) for column in rest)
        and (_is_temporal_column(rows, first) or len(rows) <= MAX_BAR_CATEGORIES)
    ):
        # One category/time column with one or more measures: several measures are several series.
        if _is_temporal_column(rows, first):
            return ChartSpec(type="line", x=first, y=list(rest), title=title)
        return ChartSpec(type="bar", x=first, y=list(rest), title=title)
    if (
        len(columns) == 3
        and not _is_numeric_column(rows, first)
        and not _is_numeric_column(rows, columns[1])
        and _is_numeric_column(rows, columns[2])
    ):
        chart = with_series(
            ChartSpec(
                type="line" if _is_temporal_column(rows, first) else "bar",
                x=first,
                y=[columns[2]],
                title=title,
            ),
            columns,
            rows,
        )
        return chart if chart.series else ChartSpec(type="table", title=title)
    if (
        len(columns) == 2
        and len(rows) >= MIN_SCATTER_POINTS
        and all(_is_numeric_column(rows, column) for column in columns)
    ):
        return ChartSpec(type="scatter", x=first, y=[columns[1]], title=title)
    if (
        len(rows) == 1
        and 2 <= len(columns) <= MAX_SERIES
        and all(_is_numeric_column(rows, column) for column in columns)
    ):
        return ChartSpec(type="bar", x=None, y=list(columns), title=title)
    return ChartSpec(type="table", title=title)
