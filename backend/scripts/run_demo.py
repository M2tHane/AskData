"""Ask the 100 demo questions as the demo accounts and write a report.

    uv run python scripts/run_demo.py [--only 1-20] [--workers 4]

Accounts come from the 0600 file written by provision_demo.py. Every question goes through the
normal chat API, so the conversations show up in each account's history (title "NNN 类别：问题").
The report lists status, chart type and row count only; result rows are never written out.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from demo_questions import QUESTIONS, Question
from provision_eval import Api

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Outcome:
    question: Question
    status: str
    code: str
    rows: int
    chart: str
    passed: bool
    seconds: float
    conversation_id: int
    note: str = ""


def chart_kind(chart: dict[str, Any] | None) -> str:
    if not chart:
        return "-"
    if chart["type"] == "bar" and chart.get("stack"):
        return "stack"
    if chart["type"] == "line" and (len(chart.get("y") or []) > 1 or chart.get("series")):
        return "multiline"
    return str(chart["type"])


def judge(expect: str, status: str, rows: int, chart: str) -> tuple[bool, str]:
    verdicts: list[tuple[bool, str]] = []
    for option in expect.split("|"):
        base, _, wanted = option.partition(":")
        if base == "ok":
            good = status == "success" and rows > 0 and (not wanted or wanted == chart)
            why = f"期望出图 {wanted}，实际 {chart}" if wanted and status == "success" else ""
        elif base == "empty":
            good, why = status == "success" and rows == 0, ""
        elif base == "refused":
            good, why = status in ("blocked", "failed") and rows == 0, ""
        elif base == "clarify":
            good, why = status == "clarify", ""
        else:
            raise ValueError(f"unknown expectation {expect}")
        if good:
            return True, ""
        verdicts.append((False, why))
    return False, next((why for _, why in verdicts if why), "")


def ask(
    api: Api,
    conversation: int,
    text: str,
    metric_ids: list[int],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"question": text, "referenced_metric_ids": metric_ids, **(extra or {})}
    for attempt in range(4):
        try:
            return dict(api.call("POST", f"/api/chat/conversations/{conversation}/messages", body))
        except SystemExit as error:
            if "RATE_LIMITED" in str(error) and attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            return {
                "status": "failed",
                "code": str(error).rsplit(": ", 1)[-1],
                "rows": [],
                "row_count": 0,
            }
    raise AssertionError("unreachable")


def run_unit(
    unit: list[Question],
    sessions: dict[str, Api],
    datasource: int,
    metrics: dict[str, dict[str, int]],
) -> list[Outcome]:
    outcomes: list[Outcome] = []
    conversation = 0
    for index, question in enumerate(unit):
        api = sessions[question.user]
        if index == 0 or question.session is None:
            title = f"{question.number:03d} {question.category}：{question.text}"[:60]
            conversation = api.call(
                "POST", "/api/chat/conversations", {"datasource_id": datasource, "title": title}
            )["id"]
        metric_ids = [
            metric_id
            for name, metric_id in metrics.get(question.user, {}).items()
            if f"@{name}" in question.text
        ]
        started = time.time()
        reply = ask(api, conversation, question.text, metric_ids)
        clarification = reply.get("clarification")
        if reply.get("status") == "clarify" and clarification and "clarify" not in question.expect:
            # answer like a user would: pick the first offered reading
            reply = ask(
                api,
                conversation,
                question.text,
                metric_ids,
                {
                    "clarification_message_id": reply.get("message_id"),
                    "clarification_choice": clarification["choices"][0],
                },
            )
        status = str(reply.get("status", "failed"))
        rows = int(reply.get("row_count") or 0)
        kind = chart_kind(reply.get("chart"))
        passed, note = judge(question.expect, status, rows, kind)
        outcomes.append(
            Outcome(
                question,
                status,
                str(reply.get("code", "")),
                rows,
                kind,
                passed,
                round(time.time() - started, 1),
                conversation,
                note,
            )
        )
    return outcomes


def parse_range(spec: str | None) -> set[int]:
    if not spec:
        return {q.number for q in QUESTIONS}
    chosen: set[int] = set()
    for part in spec.split(","):
        low, _, high = part.partition("-")
        chosen.update(range(int(low), int(high or low) + 1))
    return chosen


def write_report(path: Path, outcomes: list[Outcome]) -> None:
    done = sorted(outcomes, key=lambda o: o.question.number)
    by_category: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in done:
        by_category[outcome.question.category].append(outcome)
    lines = [
        "# 演示业务问答结果",
        "",
        f"共 {len(done)} 题，符合预期 {sum(o.passed for o in done)} 题。"
        "只记录状态、图表类型和行数，不含结果数据。",
        "",
        "| 类别 | 题数 | 符合预期 |",
        "|---|---|---|",
    ]
    for category, items in by_category.items():
        lines.append(f"| {category} | {len(items)} | {sum(o.passed for o in items)} |")
    lines += [
        "",
        "| # | 类别 | 提问人 | 问题 | 预期 | 状态 | 图表 | 行数 | 耗时 | 结果 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for o in done:
        q = o.question
        verdict = "符合" if o.passed else "**不符** " + o.note
        text, expect = q.text.replace("|", "/"), q.expect.replace("|", "/")
        lines.append(
            f"| {q.number} | {q.category} | {q.user} | {text} | {expect} | "
            f"{o.status}/{o.code} | {o.chart} | {o.rows} | {o.seconds}s | {verdict} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--accounts-file", type=Path, default=ROOT / "deploy" / ".env.demo-accounts"
    )
    parser.add_argument("--report", type=Path, default=ROOT / "docs" / "DEMO_RESULTS.md")
    parser.add_argument("--only", help="question numbers, e.g. 1-20,45")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    accounts = json.loads(args.accounts_file.read_text(encoding="utf-8"))
    datasource = int(accounts["datasource_id"])
    chosen = parse_range(args.only)
    users = sorted({q.user for q in QUESTIONS if q.number in chosen})
    base = Api(args.api)
    sessions = {name: base.login(name, accounts["users"][name]["password"]) for name in users}
    metrics = {
        name: {
            m["name"]: m["id"] for m in api.call("GET", f"/api/metric?datasource_id={datasource}")
        }
        for name, api in sessions.items()
    }

    units: list[list[Question]] = []
    chains: dict[str, list[Question]] = {}
    for question in QUESTIONS:
        if question.number not in chosen:
            continue
        if question.session is None:
            units.append([question])
        else:
            chains.setdefault(question.session, []).append(question)
    units += list(chains.values())

    outcomes: list[Outcome] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(lambda unit: run_unit(unit, sessions, datasource, metrics), units):
            outcomes += result
    write_report(args.report, outcomes)
    failed = [o for o in outcomes if not o.passed]
    sys.stdout.write(
        f"{len(outcomes)} asked, {len(outcomes) - len(failed)} as expected; report: {args.report}\n"
    )
    for o in sorted(failed, key=lambda o: o.question.number):
        sys.stdout.write(
            f"  #{o.question.number} {o.question.expect} -> {o.status}/{o.code} "
            f"rows={o.rows} chart={o.chart} {o.note}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
