"""Ask eval questions through the MCP endpoint, as an external agent would, and score them
with the eval's own grading; compare with a web-channel run of the same cases.

Run inside the api container (it calls the running endpoint on 127.0.0.1:8000):

    python scripts/eval_mcp.py --baseline-run 28 --limit 20 [--model-id 2]

A temporary connection is created with exactly the asker's grants (columns and masks) on
the case's datasource, used, and deleted at the end; its key never leaves this process.
Only cases that can be compared are chosen: single-turn, no relative dates (an MCP ask runs
on today's clock, the eval on a fixed one), no charts, clarification or uploads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from typing import Any

import httpx2
from arq import create_pool
from arq.connections import RedisSettings
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text

from app.composition import Composition
from app.core.config import get_settings
from app.core.db import Database
from app.core.errors import ErrorCode
from app.core.sql.executor import QueryResult
from app.core.sql.guard import SqlGuard
from app.modules.eval.adapters import build_reference_runner
from app.modules.eval.schemas import PipelineOutcome
from app.modules.eval.scoring import evaluate
from app.modules.mcpaccess.schemas import McpClientWrite
from app.modules.permission.schemas import ClientColumnWrite, ClientGrantsWrite

RELATIVE = re.compile(
    r"上个?月|本月|今年|去年|近\d|最近|本季|上季|昨天|今天|本周|上周|同比|环比|这个月|前\d"
)
SKIPPED = {"time", "clarify", "chart", "upload"}
ENDPOINT = "http://127.0.0.1:8000/mcp"


def comparable(cases: list[Any], limit: int) -> list[Any]:
    """Round-robin over categories, in id order, so the subset covers each kind."""
    pool: dict[str, list[Any]] = defaultdict(list)
    for case in sorted(cases, key=lambda c: c.id):
        if (
            not case.history
            and case.category not in SKIPPED
            and not RELATIVE.search(case.question)
            and case.datasource == "commerce"
        ):
            pool[case.category].append(case)
    chosen: list[Any] = []
    while len(chosen) < limit and any(pool.values()):
        for category in sorted(pool):
            if pool[category] and len(chosen) < limit:
                chosen.append(pool[category].pop(0))
    return chosen


async def with_definitions(
    database: Database, assets: Any, outcome: PipelineOutcome, body: dict[str, Any]
) -> PipelineOutcome:
    """The definitions the run used, from its query log: the MCP answer names metrics only,
    and the eval checks fixture ids exactly as the web adapter derives them."""
    async with database.session() as session:
        payload = await session.scalar(
            text("SELECT payload FROM query_run WHERE payload->>'request_id' = :id"),
            {"id": body.get("request_id", "")},
        )
    details = (payload or {}).get("details") or payload or {}
    found: dict[str, list[str]] = {}
    for kind, field in (("semantic", "semantic"), ("metric", "metrics")):
        aliases = getattr(assets.bindings, f"{kind}_aliases")
        found[kind] = [
            aliases.get(
                f"{kind}:{item.get('ref_id')}", aliases.get(item.get("key"), item.get("key"))
            )
            for item in details.get(field) or []
            if item.get("kind") == kind
        ]
    return outcome.model_copy(
        update={"semantic_ids": found["semantic"], "metric_ids": found["metric"]}
    )


async def use_default(session: Any, model_id: int) -> None:
    # Two statements: at no point are there two default LLMs.
    await session.execute(text("UPDATE model_config SET is_default = false WHERE type = 'llm'"))
    await session.execute(
        text("UPDATE model_config SET is_default = true WHERE id = :id"), {"id": model_id}
    )


def outcome_of(result: Any) -> PipelineOutcome:
    body = result.structured_content or {}
    usage = body.get("usage") or {}
    common = {
        "tokens_in": usage.get("input_tokens", 0),
        "tokens_out": usage.get("output_tokens", 0),
        "duration_ms": usage.get("duration_ms", 0),
    }
    if result.is_error:
        code = body.get("code", "")
        known = code in ErrorCode._value2member_map_
        blocked = known and (code.startswith("SQL_") or code in {"QUERY_BLOCKED", "FORBIDDEN"})
        return PipelineOutcome(
            status="blocked" if blocked else "failed",
            blocked_code=ErrorCode(code) if blocked else None,
            executed_queries=0,
            message=body.get("message", ""),
            **common,
        )
    if body.get("status") == "clarify":
        return PipelineOutcome(
            status="clarify",
            executed_queries=0,
            clarification_options=body.get("options") or [],
            message=body.get("question") or "",
            **common,
        )
    data = body.get("data") or {}
    return PipelineOutcome(
        status="success",
        sql=body.get("sql"),
        result=QueryResult(
            columns=data.get("columns", []),
            rows=data.get("rows", []),
            row_count=data.get("row_count", 0),
            truncated=bool(data.get("truncated")),
            duration_ms=0,
        ),
        executed_queries=1,
        message=body.get("answer") or "",
        **common,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-run", type=int, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--model-id", type=int, help="LLM to make the default for this run")
    args = parser.parse_args()
    settings = get_settings()
    if settings.database_url is None or settings.redis_url is None:
        raise SystemExit("database and redis settings are required")
    database = Database(settings.database_url.get_secret_value())
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url.get_secret_value()))
    runtime = Composition(settings, database, redis, worker=True)
    assets = await runtime.mapped_assets()
    cases = comparable(assets.cases, args.limit)
    admin = assets.users["admin"]
    previous_default = None
    # One connection per asker and datasource, granted exactly what that asker sees.
    connections: dict[tuple[str, str], tuple[int, str, int]] = {}
    rows: list[dict[str, Any]] = []
    try:
        async with database.transaction() as session:
            baseline = {
                row.external_id: row.passed
                for row in await session.execute(
                    text(
                        "SELECT c.external_id, r.passed FROM eval_result r "
                        "JOIN eval_case c ON c.id = r.case_id WHERE r.run_id = :run"
                    ),
                    {"run": args.baseline_run},
                )
            }
            if args.model_id:
                previous_default = await session.scalar(
                    text("SELECT id FROM model_config WHERE type='llm' AND is_default")
                )
                await use_default(session, args.model_id)
        for case in cases:
            pair = (case.as_user, case.datasource)
            if pair in connections:
                continue
            datasource = await assets.resolve(case.datasource)
            async with runtime.services(transaction=True) as services:
                scope = await services.permissions.visible_scope(
                    assets.users[case.as_user], datasource.id
                )
                catalog = await services.schema.catalog(datasource.id)
                columns = [
                    ClientColumnWrite(
                        column_id=column.id,
                        mask_rule=scope.tables[table.qualified_name].masks.get(column.name),
                    )
                    for table in catalog
                    if table.qualified_name in scope.tables
                    for column in table.columns
                    if column.name in scope.tables[table.qualified_name].columns
                ]
                client, created = await services.mcp.create(
                    McpClientWrite(
                        name=f"eval-mcp {case.as_user} {time.strftime('%H:%M')}",
                        daily_call_limit=args.limit * 2,
                        ask_timeout_seconds=120,
                    ),
                    actor=admin,
                )
                await services.mcp.replace_grants(
                    client.id, datasource.id, ClientGrantsWrite(columns=columns), actor=admin
                )
            connections[pair] = (client.id, created.key, datasource.id)
        references = build_reference_runner(assets, SqlGuard(), runtime.executor)
        for index, case in enumerate(cases, 1):
            _, key, datasource_id = connections[(case.as_user, case.datasource)]
            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=180)
            async with Client(streamable_http_client(ENDPOINT, http_client=http)) as agent:
                result = await agent.call_tool(
                    "ask",
                    {"question": case.question, "datasource_id": datasource_id, "max_rows": 1000},
                )
            outcome = await with_definitions(
                database, assets, outcome_of(result), result.structured_content or {}
            )
            reference = await references.result(case) if case.expect_status == "success" else None
            scored = evaluate(
                case, outcome, reference, await assets.hidden_names(case), exact_code=False
            )
            body = result.structured_content or {}
            rows.append(
                {
                    "case": case.id,
                    "category": case.category,
                    "web": baseline.get(case.id),
                    "mcp": scored.passed,
                    "reason": "" if scored.passed else scored.reason,
                    "status": body.get("status"),
                    "code": body.get("code"),
                    "truncated": (body.get("data") or {}).get("truncated_by"),
                    "tokens": outcome.tokens_in,
                    "seconds": round(outcome.duration_ms / 1000, 1),
                }
            )
            sys.stdout.write(f"{index:2}/{len(cases)} {json.dumps(rows[-1], ensure_ascii=False)}\n")
            sys.stdout.flush()
    finally:
        async with runtime.services(transaction=True) as services:
            for connection, _, _ in connections.values():
                await services.mcp.delete(connection, actor=admin)
        if previous_default is not None:
            async with database.transaction() as session:
                await use_default(session, previous_default)
        await redis.close()
        await database.close()
    measured = [r for r in rows if r["tokens"]]
    summary = {
        "cases": len(rows),
        "mcp_passed": sum(r["mcp"] for r in rows),
        "web_passed": sum(bool(r["web"]) for r in rows),
        "disagree": [r["case"] for r in rows if bool(r["web"]) != r["mcp"]],
        "median_seconds": statistics.median(r["seconds"] for r in measured) if measured else None,
        "mean_input_tokens": round(statistics.mean(r["tokens"] for r in measured))
        if measured
        else None,
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
