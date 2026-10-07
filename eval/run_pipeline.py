"""Replay AskData fixtures through the real chat pipeline using explicit simulated LLM outputs.

Run from backend: uv run --frozen python ../eval/run_pipeline.py --gate1
For the full suite supply --responses scripts.json and read-only connections through
ASKDATA_EVALUATION_DATASOURCES. This runner reports failures; it never obtains model
answers from expected_sql or expected result/clarification fields.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

# The independent asset environment deliberately does not install the backend.
# This executable is run with backend's frozen environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.audit import QueryLogRecord, QueryLogWriter  # noqa: E402
from app.core.datasource import Datasource  # noqa: E402
from app.core.errors import AppError, ErrorCode  # noqa: E402
from app.core.sql.dialect import Dialect  # noqa: E402
from app.core.sql.executor import MemoryQueryLimiter, SqlExecutor  # noqa: E402
from app.core.sql.guard import SqlGuard  # noqa: E402
from app.modules.eval.adapters import (  # noqa: E402
    EvaluationSettings,
    FixtureBindings,
    MappedEvalAssets,
    build_fixture_assets,
    build_reference_runner,
    build_simulated_adapter,
    run_suite,
)
from app.modules.eval.assets import EvalAssets  # noqa: E402
from app.modules.eval.schemas import SAFETY_CATEGORIES, RunRequest  # noqa: E402
from pydantic import SecretStr  # noqa: E402


class ReplayLogs:
    """Bounded query summaries in an isolated replay; CLI emits no SQL or result previews."""

    def __init__(self) -> None:
        self.records: list[QueryLogRecord] = []

    async def append(self, record: QueryLogRecord) -> int:
        self.records.append(record)
        return len(self.records)


def load_responses(path: Path, assets: MappedEvalAssets) -> dict[str, list[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise AppError(ErrorCode.VALIDATION_ERROR)
        known = {case.id: case.datasource for case in assets.cases}
        scripts: dict[str, list[str]] = {}
        for case_id, responses in raw.items():
            if case_id not in known or not isinstance(responses, list):
                raise AppError(ErrorCode.VALIDATION_ERROR)
            mapped = []
            for response in responses:
                if not isinstance(response, str):
                    raise AppError(ErrorCode.VALIDATION_ERROR)
                parsed = json.loads(response)
                if isinstance(parsed, dict) and "sql" in parsed:
                    if not isinstance(parsed["sql"], str):
                        raise AppError(ErrorCode.VALIDATION_ERROR)
                    parsed["sql"] = assets.map_sql(known[case_id], parsed["sql"])
                    response = json.dumps(parsed, ensure_ascii=False)
                mapped.append(response)
            scripts[case_id] = mapped
        return scripts
    except (OSError, ValueError):
        raise AppError(ErrorCode.VALIDATION_ERROR, "Invalid simulated response artifact") from None


async def replay(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    baseline = EvalAssets(args.assets)
    if args.gate1:
        datasources = {
            alias: Datasource(
                id=identifier,
                dialect=Dialect.POSTGRES,
                host="unused-gate1",
                database="unused-gate1",
                username="unused-gate1",
                password=SecretStr(""),
                port=5432,
                schema_name=baseline.fixtures["datasources"][alias]["schema"],
                tls=False,
            )
            for alias, identifier in baseline.datasource_ids.items()
        }
    else:
        datasources = EvaluationSettings().evaluation_datasources
    assets = build_fixture_assets(
        args.assets,
        FixtureBindings(team_ids=baseline.team_ids, users=baseline.users, datasources=datasources),
    )
    if args.gate1:
        safety = [case for case in assets.cases if case.category in SAFETY_CATEGORIES]
        scripts = {}
        for case in safety:
            if not case.candidate_sql:
                raise AppError(ErrorCode.VALIDATION_ERROR)
            # The replay pipeline has no retrieval candidates, so the clarification
            # stage never calls the model: the SQL response is the first call.
            scripts[case.id] = [
                json.dumps({"sql": assets.map_sql(case.datasource, case.candidate_sql)}),
            ]
        case_ids = [case.id for case in safety]
    else:
        scripts = load_responses(args.responses, assets)
        case_ids = args.case
    logs = ReplayLogs()
    # Replay rate limits are independent of production limits and real user identities.
    # Concurrency/resource limits, read-only connections and core SQL checks still apply.
    executor = SqlExecutor.default()
    executor.limiter = MemoryQueryLimiter(per_minute=1000)
    adapter = build_simulated_adapter(assets, scripts, executor, QueryLogWriter(logs))
    results, summary = await run_suite(
        assets,
        adapter,
        build_reference_runner(assets, SqlGuard(), executor),
        RunRequest(model_id="fixed", case_ids=case_ids),
    )
    report = {
        "mode": "gate1" if args.gate1 else "simulated_pipeline",
        "summary": summary,
        "query_log_count": len(logs.records),
        "failures": [
            {"id": item.case_id, "reason": item.reason} for item in results if not item.passed
        ],
    }
    return report, all(item.passed for item in results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=Path(__file__).resolve().parent)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--gate1", action="store_true", help="22 fixed safety outputs; no database needed"
    )
    mode.add_argument(
        "--responses", type=Path, help="case ID -> list of model JSON response strings"
    )
    parser.add_argument(
        "--case", action="append", default=[], help="select full-suite cases explicitly"
    )
    args = parser.parse_args()
    if args.gate1 and args.case:
        parser.error("--gate1 always runs the complete safety baseline")
    try:
        with redirect_stdout(sys.stderr):
            report, passed = asyncio.run(replay(args))
    except AppError as error:
        sys.stderr.write(f"Evaluation replay failed: {error.code.value}\n")
        return 2
    except Exception:
        # Settings/validation exceptions may embed raw environment inputs. Never emit them.
        sys.stderr.write(f"Evaluation replay failed: {ErrorCode.INTERNAL_ERROR.value}\n")
        return 2
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
