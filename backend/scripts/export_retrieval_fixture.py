"""Export what retrieval reads for the eval questions, for the offline recall test.

Run inside the api container with the eval bindings and (optionally) Embedding and Rerank
models configured; their answers are recorded so the test can replay them:

    python scripts/export_retrieval_fixture.py tests/fixtures/retrieval_eval.json

The eval schema and questions are fictional; nothing here is a secret.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from app.composition import Composition
from app.core.config import get_settings
from app.core.db import Database
from app.core.sql.guard import SqlGuard, referenced_tables
from app.modules.eval.schemas import EvalCaseInput


def rerank_key(query: str, texts: list[str]) -> str:
    return hashlib.sha256(json.dumps([query, texts], ensure_ascii=False).encode()).hexdigest()


async def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/retrieval_eval.json")
    settings = get_settings()
    if settings.database_url is None or settings.redis_url is None:
        raise SystemExit("database and redis settings are required")
    database = Database(settings.database_url.get_secret_value())
    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url.get_secret_value()))
    runtime = Composition(settings, database, redis, worker=True)
    path = runtime.settings.eval_assets_path / "cases.jsonl"
    cases = [EvalCaseInput.model_validate(json.loads(line)) for line in path.open()]
    assets = await runtime.mapped_assets()
    out: dict[str, Any] = {"datasources": {}, "views": {}, "targets": {}, "cases": []}
    vectors: dict[str, list[list[Any]]] = {}
    reranks: dict[str, list[int]] = {}
    async with runtime.services() as services:
        deps = services.chat().pipeline.deps
        index: Any = deps.builder.index
        if index.embedding is not None:
            nearest = index.embedding.nearest

            async def record_nearest(datasource_id: int, query: str, keys: Any, actor: Any) -> Any:
                found = await nearest(datasource_id, query, keys, actor)
                vectors[f"{datasource_id}:{query}"] = [list(key) for key in found]
                return found

            index.embedding.nearest = record_nearest
        if index.rerank is not None:
            rerank = index.rerank.rerank

            async def record_rerank(query: str, texts: list[str], context: Any) -> Any:
                ranking = await rerank(query, texts, context)
                reranks[rerank_key(query, texts)] = list(ranking)
                return ranking

            index.rerank.rerank = record_rerank
        for case in cases:
            if not case.expected_sql:
                continue
            user = assets.users[case.as_user]
            datasource = await assets.resolve(case.datasource)
            source_key = str(datasource.id)
            if source_key not in out["datasources"]:
                catalog = await services.schema.catalog(datasource.id)
                relations = await services.schema.relations(
                    datasource.id, services.permissions, actor=assets.users["admin"]
                )
                out["datasources"][source_key] = {
                    "dialect": str(datasource.dialect),
                    "tables": [table.model_dump(mode="json") for table in catalog],
                    "relations": [[r.from_column_id, r.to_column_id] for r in relations],
                }
            view_key = f"{case.as_user}:{datasource.id}"
            if view_key not in out["views"]:
                scope = await deps.permissions.visible_scope(user, datasource.id)
                semantics = await services.semantic.list(user, datasource.id)
                for term in semantics:
                    resolved = await services.semantic.targets.resolve_target(
                        term.datasource_id, term.target_type, term.target_id
                    )
                    out["targets"][f"{term.target_type}:{term.target_id}"] = [
                        resolved.table,
                        resolved.column,
                    ]
                out["views"][view_key] = {
                    "user": user.model_dump(mode="json"),
                    "scope": scope.model_dump(mode="json"),
                    "semantics": [t.model_dump(mode="json") for t in semantics],
                    "metrics": [
                        m.model_dump(mode="json")
                        for m in await services.metric.list(user, datasource.id)
                    ],
                    "examples": [
                        e.model_dump(mode="json")
                        for e in await services.feedback.examples(user, datasource.id)
                    ],
                }
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
                sorted(referenced_tables(assets.map_sql(case.datasource, inherited)))
                if inherited
                else []
            )
            follow_up = earlier[-1]["content"] if earlier and inherited else ""
            out["cases"].append(
                {
                    "id": case.id,
                    "view": view_key,
                    "datasource_id": datasource.id,
                    "question": case.question,
                    "follow_up": follow_up,
                    "keep_tables": keep,
                    "needed": {name: sorted(columns) for name, columns in needed.items()},
                }
            )
            # Run retrieval once so the vector and rerank answers are recorded.
            await deps.builder.build(
                user,
                scope,
                case.question,
                follow_up=follow_up,
                keep_tables=frozenset(keep),
            )
    out["vectors"], out["reranks"] = vectors, reranks
    await redis.close()
    await database.close()
    text = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    await asyncio.to_thread(target.write_text, text)
    sys.stdout.write(
        f"{len(out['cases'])} cases, {len(out['datasources'])} datasources, "
        f"{len(vectors)} vector answers, {len(reranks)} rerank answers -> {target}\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
