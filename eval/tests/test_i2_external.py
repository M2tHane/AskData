"""Full fixture replay against disposable PostgreSQL; simulated SQL is explicit test data.

This verifies wiring/result comparison, not model accuracy. The complete runner receives
no expected answers; only this test constructs the explicit fixed response artifact.
"""

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("app", reason="requires the backend environment")

from app.core.audit import QueryLogWriter  # noqa: E402
from app.core.datasource import Datasource  # noqa: E402
from app.core.sql.dialect import Dialect  # noqa: E402
from app.core.sql.executor import MemoryQueryLimiter, SqlExecutor  # noqa: E402
from app.core.sql.guard import SqlGuard  # noqa: E402
from app.modules.eval.adapters import (  # noqa: E402
    FixtureBindings,
    build_fixture_assets,
    build_reference_runner,
    build_simulated_adapter,
    run_suite,
)
from app.modules.eval.assets import EvalAssets  # noqa: E402
from app.modules.eval.schemas import RunRequest  # noqa: E402
from pydantic import SecretStr  # noqa: E402

EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL))

from run_pipeline import ReplayLogs, replay  # noqa: E402
from validate_cases import Postgres, check_manifest, load_database  # noqa: E402


async def command(args: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(
        lambda: subprocess.run(
            args, check=True, capture_output=True, text=True, timeout=45, **options
        )
    )


@pytest.mark.external
async def test_full_100_case_real_postgres_pipeline_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "askdata-i2-" + uuid4().hex[:12]
    secret = secrets.token_urlsafe(24)
    docker_env = {**os.environ, "POSTGRES_PASSWORD": secret}
    await command(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--env",
            "POSTGRES_PASSWORD",
            "--env",
            "POSTGRES_USER=eval_owner",
            "--env",
            "POSTGRES_DB=askdata_eval",
            "--publish",
            "127.0.0.1::5432",
            "postgres:18-alpine",
        ],
        env=docker_env,
    )
    try:
        port = int(
            (await command(["docker", "port", name, "5432/tcp"])).stdout.strip().rsplit(":", 1)[1]
        )
        deadline = time.monotonic() + 35
        while True:
            try:
                await command(
                    ["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "eval_owner"]
                )
                break
            except subprocess.CalledProcessError:
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.2)
        baseline = EvalAssets(EVAL)
        pg = Postgres(name)
        await asyncio.to_thread(
            load_database, pg, baseline.fixtures, check_manifest(baseline.fixtures)
        )
        reader_secret = secrets.token_urlsafe(24)
        statements = [
            "CREATE ROLE replay_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            f"PASSWORD '{reader_secret}';",
            "REVOKE CREATE ON SCHEMA public FROM PUBLIC;",
        ]
        connections = {}
        for alias, identifier in baseline.datasource_ids.items():
            schema = baseline.fixtures["datasources"][alias]["schema"]
            physical = "eval_commerce" if alias == "commerce" else f"ds_{identifier + 800}"
            statements += [
                f'ALTER SCHEMA "{schema}" RENAME TO "{physical}";',
                f'GRANT USAGE ON SCHEMA "{physical}" TO replay_reader;',
                f'GRANT SELECT ON ALL TABLES IN SCHEMA "{physical}" TO replay_reader;',
            ]
            connections[alias] = Datasource(
                id=identifier + 800,
                dialect=Dialect.POSTGRES,
                host="127.0.0.1",
                port=port,
                database="askdata_eval",
                username="replay_reader",
                password=SecretStr(reader_secret),
                schema_name=physical,
                tls=False,
            )
        await asyncio.to_thread(pg.run, "\n".join(statements))
        assets = build_fixture_assets(
            EVAL,
            FixtureBindings(
                team_ids=baseline.team_ids, users=baseline.users, datasources=connections
            ),
        )
        scripts = {}
        for case in assets.cases:
            if case.expect_status == "clarify":
                scripts[case.id] = [json.dumps({"clarify": True})]
            else:
                sql = case.candidate_sql or case.expected_sql
                assert sql is not None
                scripts[case.id] = [
                    json.dumps({"clarify": False}),
                    json.dumps({"sql": assets.map_sql(case.datasource, sql)}),
                    json.dumps({"type": "table"}),
                    json.dumps({"summary": "固定输出回放"}),
                ]
        executor = SqlExecutor.default()
        executor.limiter = MemoryQueryLimiter(per_minute=1000)
        logs = ReplayLogs()
        results, summary = await run_suite(
            assets,
            build_simulated_adapter(assets, scripts, executor, QueryLogWriter(logs)),
            build_reference_runner(assets, SqlGuard(), executor),
            RunRequest(model_id="fixed"),
        )
        assert len(results) == 100 and len(logs.records) == 100
        assert summary["safety_gate_passed"] and summary["blocked_pass_rate"] == 1
        assert not any(result.reason == "execution_failed" for result in results)
        assert all(
            result.passed
            for result in results
            if result.category in {"single_table", "join", "analytics", "time", "upload", "empty"}
        ), [(item.case_id, item.reason) for item in results if not item.passed]
        # Scope-only retrieval has no semantic hits or authored clarification options.
        # These limitations must be reported as failures rather than fabricated passes.
        assert all(
            not result.passed
            for result in results
            if result.category in {"semantic", "chart", "clarify"}
        )
        response_artifact = tmp_path / "responses.json"
        response_artifact.write_text(json.dumps(scripts), encoding="utf-8")
        monkeypatch.setenv(
            "ASKDATA_EVALUATION_DATASOURCES",
            json.dumps(
                {
                    alias: {
                        **datasource.model_dump(mode="json", exclude={"password"}),
                        "password": reader_secret,
                    }
                    for alias, datasource in connections.items()
                }
            ),
        )
        report, passed = await replay(
            argparse.Namespace(assets=EVAL, gate1=False, responses=response_artifact, case=[])
        )
        assert not passed and report["summary"]["total"] == 100
        assert report["summary"]["passed"] == 76 and report["summary"]["safety_gate_passed"]
        assert report["query_log_count"] == 100 and len(report["failures"]) == 24
        assert reader_secret not in json.dumps(report)
    finally:
        await command(["docker", "rm", "--force", name])
