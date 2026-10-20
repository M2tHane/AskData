"""Real HTTP/PG/Redis smoke test with a local OpenAI-compatible fixed-output model.

Run from backend: uv run python scripts/smoke.py --env-file /private/compose.env
Requires the four infrastructure services from deploy/docker-compose.local.yml.
The script migrates the meta DB and bootstraps its first admin; use a disposable project.
"""

import argparse
import asyncio
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI
from pydantic import SecretStr

from app.composition import RuntimeSettings
from app.core.logging import LogEvent, configure_logging, logger

ROOT = Path(__file__).resolve().parents[2]
mock_app = FastAPI()


class SmokeSettings(RuntimeSettings):
    smoke_schema: str = "smoke"
    smoke_reader: str = "smoke_reader"
    smoke_reader_password: SecretStr | None = None
    smoke_api_port: int = 58000
    smoke_model_port: int = 58001
    smoke_datasource_host: str = "127.0.0.1"
    smoke_datasource_port: int = 54322


@mock_app.get("/healthz")
async def model_health() -> dict[str, str]:
    return {"status": "ok"}


@mock_app.post("/v1/chat/completions")
async def model_completion(body: dict[str, Any]) -> dict[str, Any]:
    settings = SmokeSettings()
    prompt = str(body["messages"][0]["content"])
    if prompt.startswith("Determine whether"):
        text = json.dumps({"clarify": False})
    elif prompt.startswith("Return only chart"):
        text = json.dumps({"type": "table", "x": None, "y": [], "title": ""})
    elif prompt.startswith("Return strict JSON"):
        text = json.dumps({"summary": "固定冒烟结果", "filters": []})
    else:
        data = json.loads(prompt.split("<untrusted_data>", 1)[1].split("</untrusted_data>", 1)[0])
        tables = data.get("context", {}).get("tables", {})
        table = next(iter(tables), f"{settings.smoke_schema}.orders")
        sql = f"SELECT SUM(amount) AS total FROM {table}"
        if "smoke-block" in prompt:
            sql = f"DELETE FROM {table}"
        text = json.dumps({"sql": sql})
    return {
        "id": "smoke-completion",
        "object": "chat.completion",
        "created": 0,
        "model": "smoke",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def environment(path: Path) -> dict[str, str]:
    values = dotenv_values(path)
    env = dict(os.environ)
    meta_port = values.get("META_PORT") or "54321"
    import_port = values.get("IMPORT_PORT") or "54322"
    redis_port = values.get("REDIS_PORT") or "56379"
    minio_port = values.get("MINIO_PORT") or "59000"
    env.update(
        {
            "ASKDATA_DATABASE_URL": f"postgresql+asyncpg://askdata:{values['META_DB_PASSWORD']}"
            f"@127.0.0.1:{meta_port}/askdata",
            "ASKDATA_REDIS_URL": f"redis://:{values['REDIS_PASSWORD']}@127.0.0.1:{redis_port}/0",
            "ASKDATA_JWT_SECRET": str(values["JWT_SECRET"]),
            "ASKDATA_ENCRYPTION_KEY": str(values["ENCRYPTION_KEY"]),
            "ASKDATA_MINIO_ENDPOINT": f"127.0.0.1:{minio_port}",
            "ASKDATA_MINIO_ACCESS_KEY": str(values["MINIO_ACCESS_KEY"]),
            "ASKDATA_MINIO_SECRET_KEY": str(values["MINIO_SECRET_KEY"]),
            "ASKDATA_MINIO_SECURE": "false",
            "ASKDATA_IMPORT_READER_PASSWORD": str(values["IMPORT_READER_PASSWORD"]),
            "ASKDATA_IMPORT_READER_HOST": "127.0.0.1",
            "ASKDATA_IMPORT_READER_PORT": str(import_port),
            "ASKDATA_IMPORT_WRITER_URL": f"postgresql://import_writer:"
            f"{values['IMPORT_WRITER_PASSWORD']}@127.0.0.1:{import_port}/askdata_import",
            "ASKDATA_SMOKE_DATASOURCE_PORT": str(import_port),
            "ASKDATA_BOOTSTRAP_USERNAME": "admin",
            "ASKDATA_BOOTSTRAP_PASSWORD": secrets.token_hex(16),
            "ASKDATA_SMOKE_READER_PASSWORD": secrets.token_hex(16),
            "ASKDATA_SMOKE_SCHEMA": "smoke_" + secrets.token_hex(4),
            "ASKDATA_SMOKE_READER": "smoke_reader_" + secrets.token_hex(4),
            "ASKDATA_SMOKE_API_PORT": str(free_port()),
            "ASKDATA_SMOKE_MODEL_PORT": str(free_port()),
            "ASKDATA_ENVIRONMENT": "production",
            "ASKDATA_SCHEDULER_ADMIN_ID": "",
            "PYTHONPATH": str(ROOT / "backend"),
        }
    )
    return env


def run(command: list[str], env: dict[str, str], *, input_text: str | None = None) -> None:
    completed = subprocess.run(
        command,
        cwd=ROOT / "backend",
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if completed.returncode:
        # Child exception texts may contain URLs or submitted data; do not echo them.
        raise RuntimeError(f"Command failed: {command[0]} (exit {completed.returncode})")


async def wait_http(client: httpx.AsyncClient, url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            response = await client.get(url)
            if response.is_success:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.2)
    raise RuntimeError("HTTP service did not become ready within 30 seconds")


async def exercise(env: dict[str, str]) -> None:
    api = f"http://127.0.0.1:{env['ASKDATA_SMOKE_API_PORT']}"
    model = f"http://127.0.0.1:{env['ASKDATA_SMOKE_MODEL_PORT']}"
    async with httpx.AsyncClient(base_url=api, timeout=45) as client:
        await wait_http(client, api + "/healthz")
        await wait_http(client, model + "/healthz")

        async def request(method: str, path: str, data: Any = None) -> Any:
            response = await client.request(method, path, json=data)
            payload = response.json()
            if not response.is_success or payload.get("code") != "OK":
                raise RuntimeError(
                    f"Smoke HTTP step failed: {method} {path}: "
                    f"{response.status_code}/{payload.get('code')}"
                )
            return payload["data"]

        tokens = await request(
            "POST",
            "/api/auth/login",
            {
                "username": "admin",
                "password": env["ASKDATA_BOOTSTRAP_PASSWORD"],
            },
        )
        client.headers["Authorization"] = f"Bearer {tokens['access_token']}"
        team = await request("POST", "/api/org/teams", {"name": env["ASKDATA_SMOKE_SCHEMA"]})
        member_password = secrets.token_hex(16)
        await request(
            "POST",
            "/api/org/users",
            {
                "username": env["ASKDATA_SMOKE_READER"],
                "password": member_password,
                "role": "member",
                "team_id": team["id"],
            },
        )
        source = await request(
            "POST",
            "/api/datasources",
            {
                "name": env["ASKDATA_SMOKE_SCHEMA"],
                "type": "postgresql",
                "host": "127.0.0.1",
                "port": int(env["ASKDATA_SMOKE_DATASOURCE_PORT"]),
                "database": "askdata_import",
                "username": env["ASKDATA_SMOKE_READER"],
                "password": env["ASKDATA_SMOKE_READER_PASSWORD"],
                "schema_name": env["ASKDATA_SMOKE_SCHEMA"],
                "tls": False,
            },
        )
        assert source["read_only_account"] is True
        assert source["password"] == "********"
        assert await request("POST", "/api/eval/cases/load") == 100
        task = await request("POST", f"/api/schema/{source['id']}/sync")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = await request("GET", f"/api/schema/tasks/{task['id']}")
            if state["status"] == "success":
                break
            if state["status"] == "failed":
                raise RuntimeError(f"Metadata synchronization failed: {state['error']}")
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError("Metadata synchronization timed out")
        tables = await request("GET", f"/api/schema/{source['id']}/tables")
        table = next(item for item in tables if item["name"] == "orders")
        scope = await request("GET", f"/api/permissions/{source['id']}/teams/{team['id']}")
        assert scope["tables"] == [], "New metadata must be ungranted"
        await request(
            "PUT",
            f"/api/permissions/{source['id']}/teams/{team['id']}",
            {
                "tables": [
                    {
                        "table_id": table["id"],
                        "member_visible": True,
                        "columns": [
                            {"column_id": column["id"], "member_visible": True}
                            for column in table["columns"]
                        ],
                    }
                ],
            },
        )
        await request(
            "POST",
            "/api/models",
            {
                "type": "llm",
                "provider": "openai",
                "model_name": "smoke",
                "api_key": "local-smoke-only",
                "base_url": model + "/v1",
                "is_default": True,
                "params": {"retries": 0, "timeout_seconds": 10},
            },
        )
        preview = await request(
            "POST",
            "/api/upload/preview",
            {
                "filename": "smoke.csv",
                "datasource_name": "Smoke CSV",
                "owner_team_id": team["id"],
                "content_base64": base64.b64encode(b"id,amount\n1,4\n2,5\n").decode(),
                "config": {
                    "table_name": "orders",
                    "columns": [
                        {"name": "id", "data_type": "integer"},
                        {"name": "amount", "data_type": "integer"},
                    ],
                },
            },
        )
        await request(
            "POST",
            f"/api/upload/{preview['id']}/confirm",
            {
                "config": preview["config"],
                "mode": "overwrite",
            },
        )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            job = await request("GET", f"/api/upload/{preview['id']}")
            if job["status"] == "success":
                break
            if job["status"] == "failed":
                raise RuntimeError(f"Upload failed: {job['error']}")
            await asyncio.sleep(0.2)
        else:
            raise RuntimeError("Upload import timed out")
        owner_grants = await request(
            "GET", f"/api/permissions/{preview['datasource_id']}/teams/{team['id']}"
        )
        assert len(owner_grants["tables"]) == 1
        member_tokens = await request(
            "POST",
            "/api/auth/login",
            {
                "username": env["ASKDATA_SMOKE_READER"],
                "password": member_password,
            },
        )
        client.headers["Authorization"] = f"Bearer {member_tokens['access_token']}"
        conversation = await request(
            "POST",
            "/api/chat/conversations",
            {
                "datasource_id": source["id"],
                "title": "smoke",
            },
        )
        path = f"/api/chat/conversations/{conversation['id']}/messages"
        reply = await request("POST", path, {"question": "统计订单金额总和"})
        assert reply["status"] == "success", f"Query failed: {reply['code']}"
        assert len(reply["rows"]) == 1 and float(reply["rows"][0]["total"]) == 30
        assert reply["query_run_id"] is not None
        blocked = await request("POST", path, {"question": "smoke-block"})
        assert blocked["status"] == "blocked" and blocked["code"] == "SQL_NOT_SELECT"
        streamed = await client.post(path + "/stream", json={"question": "统计订单金额总和"})
        assert streamed.is_success and "event: done" in streamed.text
        upload_conversation = await request(
            "POST",
            "/api/chat/conversations",
            {
                "datasource_id": preview["datasource_id"],
                "title": "smoke upload",
            },
        )
        uploaded = await request(
            "POST",
            f"/api/chat/conversations/{upload_conversation['id']}/messages",
            {
                "question": "统计上传金额总和",
            },
        )
        assert uploaded["status"] == "success" and float(uploaded["rows"][0]["total"]) == 9
        client.headers["Authorization"] = f"Bearer {tokens['access_token']}"
        await request("PUT", f"/api/permissions/{source['id']}/teams/{team['id']}", {"tables": []})
        client.headers["Authorization"] = f"Bearer {member_tokens['access_token']}"
        revoked = await request("POST", path, {"question": "统计订单金额总和"})
        assert revoked["status"] != "success" and not revoked["rows"]
        logger.info(LogEvent.QUERY, status="smoke_passed", count=4)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--project", default="askdata-smoke")
    args = parser.parse_args()
    configure_logging()
    env = environment(args.env_file)
    schema_name, reader = env["ASKDATA_SMOKE_SCHEMA"], env["ASKDATA_SMOKE_READER"]
    password = env["ASKDATA_SMOKE_READER_PASSWORD"]
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(args.env_file.resolve()),
        "-p",
        args.project,
        "-f",
        str(ROOT / "deploy/docker-compose.yml"),
        "-f",
        str(ROOT / "deploy/docker-compose.local.yml"),
    ]
    run(
        [
            *compose,
            "exec",
            "-T",
            "pg-import",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "import_admin",
            "-d",
            "askdata_import",
        ],
        env,
        input_text=f"""
CREATE SCHEMA {schema_name};
CREATE TABLE {schema_name}.orders (id bigint PRIMARY KEY, amount bigint NOT NULL);
INSERT INTO {schema_name}.orders VALUES (1,10),(2,20);
CREATE ROLE {reader} LOGIN PASSWORD '{password}';
GRANT CONNECT ON DATABASE askdata_import TO {reader};
GRANT USAGE ON SCHEMA {schema_name} TO {reader};
GRANT SELECT ON ALL TABLES IN SCHEMA {schema_name} TO {reader};
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
""",
    )
    processes: list[subprocess.Popen[bytes]] = []
    try:
        run([sys.executable, "-m", "alembic", "upgrade", "head"], env)
        run([sys.executable, "scripts/create_admin.py"], env)
        run([sys.executable, str(ROOT / "deploy/bootstrap_minio.py")], env)
        with tempfile.TemporaryFile() as logs:
            for command in (
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "scripts.smoke:mock_app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    env["ASKDATA_SMOKE_MODEL_PORT"],
                    "--no-access-log",
                ],
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    env["ASKDATA_SMOKE_API_PORT"],
                    "--no-access-log",
                ],
                [sys.executable, "-m", "arq", "app.worker.WorkerSettings"],
            ):
                processes.append(
                    subprocess.Popen(
                        command,
                        cwd=ROOT / "backend",
                        env=env,
                        stdout=logs,
                        stderr=logs,
                    )
                )
            asyncio.run(exercise(env))
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        run(
            [
                *compose,
                "exec",
                "-T",
                "pg-import",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "import_admin",
                "-d",
                "askdata_import",
            ],
            env,
            input_text=f"""
DROP SCHEMA {schema_name} CASCADE;
DROP OWNED BY {reader};
DROP ROLE {reader};
""",
        )


if __name__ == "__main__":
    main()
