"""Real-LLM 100-case baseline through the production chat pipeline on disposable infrastructure.

Run from backend: uv run python scripts/eval_baseline.py --tag v1 [--limit N] [--concurrency 4]
Starts one disposable pgvector PostgreSQL (meta + fixture databases) and one Redis, provisions
org/datasource/grants/semantic/metric/model assets through public services, then replays the
evaluation suite through build_eval_adapter (real chat QueryPipeline, real PermissionService,
SqlGuard and SqlExecutor). The model endpoint and key are read only from the
ASKDATA_BASELINE_BASE_URL / _MODEL / _API_KEY environment variables and never written to the
repository or the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, cast

from cryptography.fernet import Fernet
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "backend"))

from alembic import command as alembic_command  # noqa: E402
from alembic.config import Config  # noqa: E402
from validate_cases import (  # type: ignore[import-not-found]  # noqa: E402
    Postgres,
    check_manifest,
    load_database,
)

from app.composition import Composition, PinnedModel, RuntimeSettings  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import Database  # noqa: E402
from app.core.deps import CurrentUser, Role  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.llm import (  # noqa: E402
    ChatMessage,
    ChatResponse,
    LiteLLMTransport,
    LLMClient,
    ModelConfig,
    ModelRegistry,
)
from app.core.masking import MaskKind, MaskRule  # noqa: E402
from app.core.scope import ScopeLevel  # noqa: E402
from app.core.sql.executor import MemoryQueryLimiter  # noqa: E402
from app.core.sql.guard import SqlGuard  # noqa: E402
from app.modules.datasource.schemas import DatasourceWrite  # noqa: E402
from app.modules.eval.adapters import (  # noqa: E402
    build_eval_adapter,
    build_reference_runner,
)
from app.modules.eval.assets import EvalAssets  # noqa: E402
from app.modules.eval.schemas import EvalCaseInput, Evaluation, RunRequest  # noqa: E402
from app.modules.eval.scoring import evaluate, summarize  # noqa: E402
from app.modules.eval.service import EvalQuestion, case_clock  # noqa: E402
from app.modules.metric.schemas import MetricInput  # noqa: E402
from app.modules.modelcfg.schemas import ModelParameters, ModelWrite  # noqa: E402
from app.modules.org.schemas import TeamWrite, UserWrite  # noqa: E402
from app.modules.permission.schemas import (  # noqa: E402
    ColumnGrantWrite,
    TableGrantWrite,
    TeamGrantsWrite,
)
from app.modules.semantic.schemas import SemanticInput  # noqa: E402
from tests.fakes import MemoryScopeCache  # noqa: E402

EVAL = ROOT / "eval"
ARQ_REDIS: Any = object()  # The baseline never enqueues background tasks.
MASK_RULES = {
    "phone": MaskRule(kind=MaskKind.PHONE),
    "bank_account": MaskRule(kind=MaskKind.FULL),
}


class ThrottledTransport(LiteLLMTransport):
    """Global pacing so the local endpoint's burst limiter is not triggered.

    Timeouts and rejections are failures, not blocks: record them and move on.
    """

    def __init__(self, interval: float = 10.0) -> None:
        super().__init__()
        self.interval = interval
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def _slot(self) -> None:
        async with self._lock:
            delay = self._next_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_at = time.monotonic() + self.interval

    async def chat(self, model: ModelConfig, messages: list[ChatMessage]) -> ChatResponse:
        await self._slot()
        return await LiteLLMTransport.chat(self, model, messages)


async def shell(args: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(
        lambda: subprocess.run(
            args, check=True, capture_output=True, text=True, timeout=120, **options
        )
    )


async def wait_postgres(container: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            await shell(["docker", "exec", container, "pg_isready", "-U", "eval_owner"])
            return
        except subprocess.CalledProcessError:
            await asyncio.sleep(0.3)
    raise RuntimeError("PostgreSQL did not become ready")


async def published_port(container: str, private: int) -> int:
    output = (await shell(["docker", "port", container, f"{private}/tcp"])).stdout
    return int(output.strip().rsplit(":", 1)[1])


async def stop_containers(pg: str, redis: str) -> None:
    def remove(name: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=60)

    for container in (pg, redis):
        await asyncio.to_thread(remove, container)


async def start_containers(prefix: str) -> tuple[str, str, str, int, int, str]:
    pg, redis = f"{prefix}-pg", f"{prefix}-redis"
    redis_password = secrets.token_urlsafe(16)
    pg_password = secrets.token_urlsafe(24)
    await shell(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            pg,
            "--env",
            "POSTGRES_PASSWORD",
            "--env",
            "POSTGRES_USER=eval_owner",
            "--env",
            "POSTGRES_DB=askdata_eval",
            "--publish",
            "127.0.0.1::5432",
            "pgvector/pgvector:pg18",
        ],
        env={**os.environ, "POSTGRES_PASSWORD": pg_password},
    )
    await shell(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            redis,
            "--publish",
            "127.0.0.1::6379",
            "redis:7-alpine",
            "redis-server",
            "--requirepass",
            redis_password,
        ],
    )
    try:
        await wait_postgres(pg)
        pg_port = await published_port(pg, 5432)
        redis_port = await published_port(redis, 6379)
        await shell(
            [
                "docker",
                "exec",
                pg,
                "psql",
                "-U",
                "eval_owner",
                "-d",
                "askdata_eval",
                "-c",
                "CREATE DATABASE askdata_meta",
            ],
        )
    except BaseException:
        await stop_containers(pg, redis)
        raise
    return redis_password, pg, redis, pg_port, redis_port, pg_password


async def migrate_meta() -> None:
    configuration = Config(str(ROOT / "backend/alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "backend/migrations"))
    await asyncio.to_thread(alembic_command.upgrade, configuration, "head")


def physical_names(baseline: EvalAssets) -> dict[str, str]:
    # Same convention as the I2 full replay: fixture schemas become distinct physical names.
    return {
        alias: "eval_commerce" if alias == "commerce" else f"ds_{identifier + 800}"
        for alias, identifier in baseline.datasource_ids.items()
    }


async def provision(
    runtime: Composition, baseline: EvalAssets, pg_port: int, reader_password: str
) -> dict[str, Any]:
    async with runtime.services(transaction=True) as services:
        admin_row = await services.org.bootstrap_admin("admin", SecretStr(secrets.token_hex(16)))
        admin = CurrentUser(id=admin_row.id, role=Role.ADMIN)
        sales = await services.org.create_team(TeamWrite(name="sales"), actor=admin)
        finance = await services.org.create_team(TeamWrite(name="finance"), actor=admin)
        users: dict[str, CurrentUser] = {"admin": admin}
        for name, role, team in (
            ("sales_leader", Role.LEADER, sales.id),
            ("sales_member", Role.MEMBER, sales.id),
            ("finance_leader", Role.LEADER, finance.id),
            ("finance_member", Role.MEMBER, finance.id),
        ):
            created = await services.org.create_user(
                UserWrite(
                    username=name,
                    password=SecretStr(secrets.token_hex(16)),
                    role=role,
                    team_id=team,
                ),
                actor=admin,
            )
            users[name] = CurrentUser(id=created.id, role=role, team_id=team)

        datasource_ids: dict[str, int] = {}
        schemas = physical_names(baseline)
        for alias in baseline.datasource_ids:
            datasource_read = await services.datasource.create(
                DatasourceWrite(
                    name=f"eval-{alias}",
                    type="postgresql",
                    host="127.0.0.1",
                    port=pg_port,
                    database="askdata_eval",
                    username="replay_reader",
                    password=SecretStr(reader_password),
                    schema_name=schemas[alias],
                    tls=False,
                ),
                actor=admin,
            )
            datasource_ids[alias] = datasource_read.id
            await services.schema.synchronize(datasource_read.id, services.grants, actor=admin)

        # Team grants mirror fixtures.yaml: leader_columns is the team grant and
        # member_columns its member subset; masks follow the fixture declaration.
        for team_alias, team_id in (("sales", sales.id), ("finance", finance.id)):
            for ds_alias, tables in baseline.fixtures["grants"][team_alias].items():
                catalog = await services.schema.catalog(datasource_ids[ds_alias])
                by_name = {table.name: table for table in catalog}
                writes = []
                for table_name, spec in tables.items():
                    table = by_name[table_name]
                    members = set(spec.get("member_columns") or [])
                    masks = cast(dict[str, str], spec.get("masks") or {})
                    writes.append(
                        TableGrantWrite(
                            table_id=table.id,
                            member_visible=bool(members),
                            columns=[
                                ColumnGrantWrite(
                                    column_id=column.id,
                                    member_visible=column.name in members,
                                    mask_rule=MASK_RULES.get(masks.get(column.name, "")),
                                )
                                for column in table.columns
                                if column.name in spec["leader_columns"]
                            ],
                        )
                    )
                await services.grants.replace(
                    datasource_ids[ds_alias], team_id, TeamGrantsWrite(tables=writes), actor=admin
                )

        semantic_aliases: dict[str, str] = {}
        commerce_catalog = await services.schema.catalog(datasource_ids["commerce"])
        columns = {
            (table.name, column.name): column.id
            for table in commerce_catalog
            for column in table.columns
        }
        for semantic in baseline.fixtures["semantics"]:
            table_name, column_name = str(semantic["target"]).split(".")
            target_column = columns[table_name, column_name]
            item = await services.semantic.save(
                SemanticInput(
                    datasource_id=datasource_ids["commerce"],
                    scope=ScopeLevel.PLATFORM,
                    target_type="column",
                    target_id=target_column,
                    aliases=[str(alias) for alias in semantic["aliases"]],
                    description=str(semantic["definition"]),
                ),
                actor=admin,
            )
            # Retrieval keys are semantic:<target_type>:<column_id>; the adapter also
            # resolves semantic:<entry ref_id>. Register both spellings.
            semantic_aliases[f"semantic:column:{target_column}"] = str(semantic["id"])
            semantic_aliases[f"semantic:{item.id}"] = str(semantic["id"])

        metric_aliases: dict[str, str] = {}
        physical = schemas["commerce"]
        owners = {
            "platform": admin,
            "team": users["sales_leader"],
            "personal": users["sales_member"],
        }
        for fixture_metric in baseline.fixtures["metrics"]:
            metric_item = await services.metric.save(
                MetricInput(
                    datasource_id=datasource_ids["commerce"],
                    scope=ScopeLevel(str(fixture_metric["scope"])),
                    name=str(fixture_metric["name"]),
                    description=str(fixture_metric["definition"]),
                    sql_template=str(fixture_metric["sql_template"]).replace(
                        "commerce.", f"{physical}."
                    ),
                ),
                actor=owners[str(fixture_metric["scope"])],
            )
            metric_aliases[f"metric:{metric_item.id}"] = str(fixture_metric["id"])

        model = await services.models.create(
            ModelWrite(
                type="llm",
                provider="openai",
                base_url=os.environ["ASKDATA_BASELINE_BASE_URL"],
                model_name=os.environ["ASKDATA_BASELINE_MODEL"],
                api_key=SecretStr(os.environ["ASKDATA_BASELINE_API_KEY"]),
                # Match the deployed configuration; override to compare protocols or effort.
                params=ModelParameters.model_validate(
                    {
                        "timeout_seconds": 120,
                        "retries": 1,
                        "api": os.environ.get("ASKDATA_BASELINE_API", "chat"),
                        "reasoning_effort": os.environ.get("ASKDATA_BASELINE_EFFORT", "medium"),
                    }
                ),
                is_default=True,
            ),
            actor=admin,
        )
    runtime.settings = runtime.settings.model_copy(
        update={
            "eval_team_ids": {"sales": sales.id, "finance": finance.id},
            "eval_user_ids": {name: user.id for name, user in users.items()},
            "eval_datasource_ids": datasource_ids,
            "eval_semantic_aliases": semantic_aliases,
            "eval_metric_aliases": metric_aliases,
        }
    )
    return {"model_id": str(model.id)}


async def run_case(
    runtime: Composition,
    assets: Any,
    runner: Any,
    case: EvalCaseInput,
    model_id: str,
    transport: ThrottledTransport,
) -> Evaluation:
    async with runtime.services() as services:

        async def dependencies(question: EvalQuestion, config: RunRequest) -> Any:
            deps = services.chat().pipeline.deps
            model = await services.models.registry.get(model_id, "llm")
            deps.llm = LLMClient(ModelRegistry(PinnedModel(model)), services.usage, transport)
            threshold = config.config.get("retrieval_threshold", 30)
            if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
                raise ValueError("invalid retrieval threshold")
            deps.builder.threshold = threshold
            return deps

        adapter = build_eval_adapter(assets, dependencies)
        try:
            reference = await runner.result(case) if case.expect_status == "success" else None
            outcome = await adapter.run(
                EvalQuestion(
                    case_id=case.id,
                    datasource=case.datasource,
                    as_user=case.as_user,
                    question=case.question,
                    history=case.history,
                    clock=case_clock(case, assets.fixtures["clock"]),
                    injection_context=case.injection_context,
                ),
                RunRequest(model_id=model_id),
            )
            result = evaluate(case, outcome, reference, await assets.hidden_names(case))
        except Exception as error:  # Report and continue; never fail the whole suite.
            detail = (
                f"{error.code.value}: {error.message}"
                if isinstance(error, AppError)
                else type(error).__name__
            )
            sys.stderr.write(f"case {case.id} crashed: {detail}\n")
            result = Evaluation(
                case_id=case.id, category=case.category, passed=False, reason="execution_failed"
            )
        await services.session.commit()
        return result


async def run_suite(
    runtime: Composition,
    assets: Any,
    cases: list[EvalCaseInput],
    model_id: str,
    concurrency: int,
    transport: ThrottledTransport,
) -> list[Evaluation]:
    runner = build_reference_runner(assets, SqlGuard(), runtime.executor)
    semaphore = asyncio.Semaphore(concurrency)
    progress = {"done": 0}

    async def worker(case: EvalCaseInput) -> Evaluation:
        async with semaphore:
            result = await run_case(runtime, assets, runner, case, model_id, transport)
            progress["done"] += 1
            sys.stderr.write(
                f"[{progress['done']}/{len(cases)}] {case.id} {case.category} "
                f"passed={result.passed} reason={result.reason}\n"
            )
            return result

    return list(await asyncio.gather(*(worker(case) for case in cases)))


async def main_async(args: argparse.Namespace) -> int:
    for name in (
        "ASKDATA_BASELINE_BASE_URL",
        "ASKDATA_BASELINE_MODEL",
        "ASKDATA_BASELINE_API_KEY",
    ):
        if not os.environ.get(name):
            raise SystemExit(f"environment variable {name} is required")
    prefix = "askdata-baseline-" + uuid.uuid4().hex[:8]
    baseline = EvalAssets(EVAL)
    redis_password, pg, redis, pg_port, redis_port, pg_password = await start_containers(prefix)
    runtime: Composition | None = None
    try:
        pg_conn = Postgres(pg)
        await asyncio.to_thread(
            load_database, pg_conn, baseline.fixtures, check_manifest(baseline.fixtures)
        )
        reader_password = secrets.token_urlsafe(24)
        statements = [
            "CREATE ROLE replay_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            f"PASSWORD '{reader_password}';",
            "REVOKE CREATE ON SCHEMA public FROM PUBLIC;",
        ]
        fixtures_schemas = baseline.fixtures["datasources"]
        for alias, physical in physical_names(baseline).items():
            statements += [
                f'ALTER SCHEMA "{fixtures_schemas[alias]["schema"]}" RENAME TO "{physical}";',
                f'GRANT USAGE ON SCHEMA "{physical}" TO replay_reader;',
                f'GRANT SELECT ON ALL TABLES IN SCHEMA "{physical}" TO replay_reader;',
            ]
        await asyncio.to_thread(pg_conn.run, "\n".join(statements))

        meta_url = f"postgresql+asyncpg://eval_owner:{pg_password}@127.0.0.1:{pg_port}/askdata_meta"
        os.environ["ASKDATA_DATABASE_URL"] = meta_url
        os.environ["ASKDATA_REDIS_URL"] = f"redis://:{redis_password}@127.0.0.1:{redis_port}/0"
        os.environ["ASKDATA_JWT_SECRET"] = secrets.token_hex(32)
        os.environ["ASKDATA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
        get_settings.cache_clear()
        await migrate_meta()

        from app.core.config import Settings

        settings = RuntimeSettings(
            **Settings(
                database_url=SecretStr(meta_url),
                redis_url=SecretStr(os.environ["ASKDATA_REDIS_URL"]),
                jwt_secret=SecretStr(os.environ["ASKDATA_JWT_SECRET"]),
                encryption_key=SecretStr(os.environ["ASKDATA_ENCRYPTION_KEY"]),
            ).model_dump()
        )
        database = Database(meta_url)
        runtime = Composition(settings, database, ARQ_REDIS)
        runtime.cache = cast("Any", MemoryScopeCache())
        runtime.executor.limiter = MemoryQueryLimiter(per_minute=1000)
        provisioned = await provision(runtime, baseline, pg_port, reader_password)
        assets = await runtime.mapped_assets()
        cases = list(assets.cases)
        if args.ids:
            selected = {
                identifier.strip() for identifier in args.ids.split(",") if identifier.strip()
            }
            cases = [case for case in cases if case.id in selected]
            missing = selected - {case.id for case in cases}
            if missing:
                raise SystemExit(f"unknown case IDs: {sorted(missing)}")
        if args.limit:
            cases = cases[: args.limit]
        started = time.monotonic()
        transport = ThrottledTransport(interval=args.interval)
        results = await run_suite(
            runtime, assets, cases, provisioned["model_id"], args.concurrency, transport
        )
        by_id = {case.id: case for case in cases}
        report = {
            "tag": args.tag,
            "model": f"openai/{os.environ['ASKDATA_BASELINE_MODEL']}",
            "base_url": os.environ["ASKDATA_BASELINE_BASE_URL"],
            "date": time.strftime("%Y-%m-%d"),
            "cases": len(cases),
            "concurrency": args.concurrency,
            "wall_seconds": int(time.monotonic() - started),
            "summary": summarize(results),
            "failures": [
                {
                    "id": item.case_id,
                    "category": by_id[item.case_id].category,
                    "as_user": by_id[item.case_id].as_user,
                    "reason": item.reason,
                    "retries": item.retries,
                }
                for item in results
                if not item.passed
            ],
            "results": [item.model_dump(mode="json") for item in results],
        }
        output = args.out or f"/tmp/eval-baseline-{args.tag}.json"
        await asyncio.to_thread(
            lambda: Path(output).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        )
        sys.stdout.write(
            json.dumps(
                {key: value for key, value in report.items() if key != "results"},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        return 0
    finally:
        if runtime is not None:
            await runtime.database.close()
        await stop_containers(pg, redis)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between model calls")
    parser.add_argument(
        "--ids",
        default="",
        help="comma-separated case IDs; empty runs every case",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
