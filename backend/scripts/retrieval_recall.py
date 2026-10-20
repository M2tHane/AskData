"""Retrieval recall on the eval questions, without calling a chat model.

For every question with a reference SQL, build the context the SQL model would get and check
that it holds every table and column the reference SQL reads. Also reports how big the
context is. Run inside the api container (it needs the eval bindings):

    python scripts/retrieval_recall.py [--full] [--case S01,J11] [--show-misses]

With an Embedding model configured the vector side takes part; without one, keyword only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys

from arq import create_pool
from arq.connections import RedisSettings

from app.composition import Composition
from app.core.config import get_settings
from app.core.db import Database
from app.core.sql.guard import SqlGuard, referenced_tables
from app.modules.eval.schemas import EvalCaseInput


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="measure the full context instead")
    parser.add_argument("--case", default="", help="comma-separated case ids")
    parser.add_argument("--show-misses", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if settings.database_url is None or settings.redis_url is None:
        raise SystemExit("database and redis settings are required")
    database = Database(settings.database_url.get_secret_value())
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url.get_secret_value()))
    runtime = Composition(settings, database, redis, worker=True)
    path = runtime.settings.eval_assets_path / "cases.jsonl"
    cases = [EvalCaseInput.model_validate(json.loads(line)) for line in path.open()]
    wanted = set(filter(None, args.case.split(",")))
    assets = await runtime.mapped_assets()
    rows: list[dict[str, object]] = []
    async with runtime.services() as services:
        deps = services.chat().pipeline.deps
        for case in cases:
            if not case.expected_sql or (wanted and case.id not in wanted):
                continue
            user = assets.users[case.as_user]
            datasource = await assets.resolve(case.datasource)
            scope = await deps.permissions.visible_scope(user, datasource.id)
            needed = (
                SqlGuard()
                .check(
                    assets.map_sql(case.datasource, case.expected_sql), datasource.dialect, scope
                )
                .columns
            )
            earlier = [turn for turn in case.history if turn.get("role") == "user"]
            inherited = next(
                (turn["sql"] for turn in reversed(case.history) if turn.get("sql")), None
            )
            keep = (
                referenced_tables(assets.map_sql(case.datasource, inherited))
                if inherited
                else frozenset()
            )
            bundle = await deps.builder.build(
                user,
                scope,
                case.question,
                full=args.full,
                follow_up=earlier[-1]["content"] if earlier and inherited else "",
                keep_tables=keep,
            )
            missing = {
                table: sorted(columns - set(bundle.tables.get(table, {})))
                for table, columns in needed.items()
                if columns - set(bundle.tables.get(table, {})) or table not in bundle.tables
            }
            context = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False)
            rows.append(
                {
                    "case": case.id,
                    "mode": bundle.mode,
                    "tables": len(bundle.tables),
                    "columns": sum(len(columns) for columns in bundle.tables.values()),
                    "items": len(bundle.items),
                    "chars": len(context),
                    "missing": missing,
                }
            )
    await redis.close()
    await database.close()
    hits = [row for row in rows if not row["missing"]]
    lines = [f"cases {len(rows)}  recall {len(hits)}/{len(rows)}"]
    for key in ("tables", "columns", "items", "chars"):
        values = [int(str(row[key])) for row in rows]
        lines.append(f"{key:8s} mean {statistics.mean(values):8.1f}  max {max(values)}")
    modes = [str(row["mode"]) for row in rows]
    lines.append(f"modes { ({mode: modes.count(mode) for mode in sorted(set(modes))}) }")
    if args.show_misses:
        lines += [
            f"MISS {row['case']} {row['mode']} {json.dumps(row['missing'], ensure_ascii=False)}"
            for row in rows
            if row["missing"]
        ]
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
