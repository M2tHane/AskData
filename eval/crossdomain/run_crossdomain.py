"""Run the cross-domain questions through the production evaluation path, inside the stack.

The evaluation worker can only ask questions of the fixture set baked into its image
(ASKDATA_EVAL_ASSETS_PATH=/app/eval). Rather than rebuild the image or change the running
services' environment, this script runs in a one-off process inside the worker container with
the asset path and fixture bindings pointed at eval/crossdomain/, and scores every question
with EvalService._scored: the same code a worker runs for each question of a run (reference
SQL through PermissionService -> SqlGuard -> SqlExecutor, the question through the real chat
QueryPipeline with the registered model, AskData's per-user query limiter with back-off, and
the same evaluate() with exact_code=False). Nothing is written to eval_case / eval_run.

    docker cp eval/crossdomain askdata-worker-1:/tmp/xd
    docker exec -w /app/backend -e ASKDATA_EVAL_ASSETS_PATH=/tmp/xd \
        -e ASKDATA_EVAL_TEAM_IDS=... (from bindings.json) askdata-worker-1 \
        python /tmp/xd/run_crossdomain.py --model 1 --run 1 --output /tmp/xd/run1.json

stdout carries one line per question (id, verdict, reason) and the summary; no SQL or data.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from arq.connections import ArqRedis

from app.composition import Composition
from app.core.config import get_settings
from app.core.db import Database
from app.modules.eval.schemas import RunRequest
from app.modules.eval.scoring import summarize


async def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    assert settings.database_url is not None
    database = Database(settings.database_url.get_secret_value())
    redis = ArqRedis.from_url(settings.redis_url.get_secret_value())
    runtime = Composition(settings, database, redis)
    request = RunRequest(model_id=args.model, config={}, note=f"crossdomain #{args.run}")
    request.pipeline_options()
    results = []
    started = time.time()
    try:
        async with runtime.services() as services:
            service = services.evaluation()
            cases = [
                case for case in service.assets.cases if not args.case or case.id in args.case
            ]
            for case in cases:
                result = await service._scored(case, -args.run, request)
                results.append(result)
                print(
                    f"{case.id} {case.category} {'PASS' if result.passed else 'FAIL'} "
                    f"{result.reason} status={result.actual_status} retries={result.retries} "
                    f"{result.duration_ms}ms",
                    flush=True,
                )
    finally:
        await redis.aclose()
        await database.close()
    summary = summarize(results)
    Path(args.output).write_text(
        json.dumps(
            {
                "run": args.run,
                "model_id": args.model,
                "config": request.config,
                "started_at": started,
                "finished_at": time.time(),
                "summary": summary,
                "results": [r.model_dump(mode="json") for r in results],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(json.dumps({k: summary[k] for k in ("total", "passed", "pass_rate")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="AskData model config id")
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--case", action="append", default=[])
    asyncio.run(main(parser.parse_args()))
