"""Calendar ranges named in a question, resolved before any model call."""

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.core.errors import AppError, ErrorCode


def resolve_time_ranges(question: str, timezone: str, now: datetime) -> dict[str, Any]:
    today = now.astimezone(ZoneInfo(timezone)).date()
    ranges: dict[str, Any] = {"timezone": timezone, "today": today.isoformat()}
    tomorrow = today + timedelta(days=1)
    if "上个月" in question:
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        ranges["上个月"] = {"start": start.isoformat(), "end_exclusive": end.isoformat()}
    for match in re.finditer(r"(?:近|最近)\s*(\d{1,3})\s*天", question):
        days = int(match[1])
        if days < 1:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        ranges[match[0]] = {
            "start": (tomorrow - timedelta(days=days)).isoformat(),
            "end_exclusive": tomorrow.isoformat(),
        }
    if "本季度" in question:
        month = ((today.month - 1) // 3) * 3 + 1
        start = date(today.year, month, 1)
        end = date(today.year + 1, 1, 1) if month == 10 else date(today.year, month + 3, 1)
        ranges["本季度"] = {"start": start.isoformat(), "end_exclusive": end.isoformat()}
    if "去年同期" in question:
        # Same elapsed year-to-date period; Feb 29 is clamped to Feb 28.
        try:
            last_day = today.replace(year=today.year - 1)
        except ValueError:
            last_day = date(today.year - 1, 2, 28)
        ranges["去年同期"] = {
            "start": date(today.year - 1, 1, 1).isoformat(),
            "end_exclusive": (last_day + timedelta(days=1)).isoformat(),
        }
    ranges.update(month_ranges(question, today))
    ranges.update(complete_ranges(question, today))
    return ranges


_CN_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
# "最近4个完整的周", "近七个完整自然日", "最近3个完整月": whole periods before the current one.
_COMPLETE = re.compile(
    r"(?:近|最近)\s*(\d{1,3}|[一二两三四五六七八九十]{1,3})\s*个?\s*完整的?\s*(?:自然)?"
    r"(日|天|周|星期|月)"
)


def _count(text: str) -> int:
    if text.isdigit():
        return int(text)
    if "十" not in text:
        return _CN_DIGITS[text]
    tens, _, ones = text.partition("十")
    return (_CN_DIGITS[tens] if tens else 1) * 10 + (_CN_DIGITS[ones] if ones else 0)


def complete_ranges(question: str, today: date) -> dict[str, dict[str, str]]:
    """Resolve "the last N complete days/weeks/months": they end where the current one starts.

    Weeks run Monday to Sunday.
    """
    ranges: dict[str, dict[str, str]] = {}
    for match in _COMPLETE.finditer(question):
        count, unit = _count(match[1]), match[2]
        if count < 1:
            raise AppError(ErrorCode.VALIDATION_ERROR)
        if unit in {"日", "天"}:
            end = today
            start = end - timedelta(days=count)
        elif unit in {"周", "星期"}:
            end = today - timedelta(days=today.weekday())
            start = end - timedelta(weeks=count)
        else:
            end = today.replace(day=1)
            months = end.year * 12 + end.month - 1 - count
            start = date(months // 12, months % 12 + 1, 1)
        ranges[match[0]] = {"start": start.isoformat(), "end_exclusive": end.isoformat()}
    return ranges


_CN_MONTHS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_MONTHS |= {"十": 10, "十一": 11, "十二": 12}
# A named month ("9月", "九月份", "2025年2月", "去年2月"), not a span ("3个月", "近三月") or a
# day ("6月20日").
_MONTH = re.compile(
    r"(?:(\d{4})\s*年\s*|(今年|去年|前年)\s*)?"
    r"(?<![\d个半每近前后内第上下本这])"
    r"(1[0-2]|0?[1-9]|十[一二]?|[一二三四五六七八九])\s*月"
    r"(?!\s*[\d一二三四五六七八九十]+\s*[日号])"
)
_YEAR = re.compile(r"(\d{4})\s*年")


def month_ranges(question: str, today: date) -> dict[str, dict[str, str]]:
    """Calendar months named in the question.

    A month without a year takes the year last written before it in the question
    ("2025年1月、2月"); with none, it is the latest such month that has begun.
    """
    ranges: dict[str, dict[str, str]] = {}
    relative = {"今年": 0, "去年": 1, "前年": 2}
    # The year written last before this point, as (position, year).
    stated = [(m.start(), int(m[1])) for m in _YEAR.finditer(question)]
    for match in _MONTH.finditer(question):
        number = match[3]
        month = int(number) if number.isdigit() else _CN_MONTHS[number]
        earlier = [year for at, year in stated if at < match.start()]
        if match[1]:
            year = int(match[1])
        elif match[2]:
            year = today.year - relative[match[2]]
            stated = sorted([*stated, (match.start(), year)])
        elif earlier:
            year = earlier[-1]
        else:
            year = today.year if month <= today.month else today.year - 1
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        key = re.sub(r"\s+", "", match[0])
        ranges[key] = {"start": start.isoformat(), "end_exclusive": end.isoformat()}
    return ranges
