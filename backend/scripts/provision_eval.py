"""Provision the evaluation fixtures on a running Compose stack, through public APIs only.

Run from backend, once per stack, with the stack up (see docs/RUNBOOK.md, 评测环境):

    ASKDATA_PROVISION_USERNAME=admin ASKDATA_PROVISION_PASSWORD=... \
        uv run python scripts/provision_eval.py --env-file ../deploy/.env

What it does:
1. Loads eval/testdb.sql and the upload fixtures into the stack's pg-import database
   (through `docker exec` into the pg-import container as its local admin), renames the
   fixture schemas to distinct physical names, and creates a SELECT-only `replay_reader` role.
2. Through the REST API as the admin: creates the fixture teams/users, registers one
   read-only datasource per fixture alias, synchronizes metadata, applies the fixture grants,
   and creates the platform/team/personal semantics and metrics.
3. Writes EVAL_TEAM_IDS / EVAL_USER_IDS / EVAL_DATASOURCE_IDS / EVAL_SEMANTIC_ALIASES /
   EVAL_METRIC_ALIASES into the given env file (0600), which Compose passes to api and worker.
   Recreate them afterwards: docker compose ... up -d api worker.

Credentials come from the environment, never from argv; fixture users get random passwords
that are used once and discarded. Safe to re-run after a partial failure (existing teams,
users, datasources, semantics and metrics are reused); refuses once the bindings are written.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))

from validate_cases import (  # type: ignore[import-not-found]  # noqa: E402
    Postgres,
    check_manifest,
    load_database,
)

from app.modules.eval.assets import EvalAssets  # noqa: E402

BINDING_KEYS = (
    "EVAL_TEAM_IDS",
    "EVAL_USER_IDS",
    "EVAL_DATASOURCE_IDS",
    "EVAL_SEMANTIC_ALIASES",
    "EVAL_METRIC_ALIASES",
)
MASK_KINDS = {"phone": "phone", "bank_account": "full"}


class ImportAdmin(Postgres):  # type: ignore[misc]
    """psql inside the pg-import container as its local administrator."""

    def __init__(self, container: str) -> None:
        super().__init__(container)

    def run(self, sql: str) -> str:
        command = [
            "docker",
            "exec",
            "-i",
            str(self.container),
            "psql",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-P",
            "format=csv",
            "-P",
            "footer=off",
            "-U",
            "import_admin",
            "-d",
            "askdata_import",
        ]
        result = subprocess.run(
            command, input=sql, capture_output=True, text=True, timeout=120, check=False
        )
        if result.returncode:
            # Never echo SQL, connection details or query output.
            raise SystemExit("PostgreSQL command failed (is the pg-import container running?)")
        return result.stdout


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token: str | None = None

    def call(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.base + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as error:
            code = json.loads(error.read() or b"{}").get("code", error.code)
            raise SystemExit(f"{method} {path} failed: {code}") from None
        return payload["data"]

    def login(self, username: str, password: str) -> Api:
        session = Api(self.base)
        session.token = session.call(
            "POST", "/api/auth/login", {"username": username, "password": password}
        )["access_token"]
        return session


def wait_task(admin: Api, task_id: str) -> None:
    for _ in range(120):
        task = admin.call("GET", f"/api/schema/tasks/{task_id}")
        if task["status"] == "success":
            return
        if task["status"] == "failed":
            raise SystemExit(f"metadata sync failed: {task.get('error')}")
        time.sleep(1)
    raise SystemExit("metadata sync timed out")


def physical_names(assets: EvalAssets) -> dict[str, str]:
    # Same convention as scripts/eval_baseline.py: fixture schemas get distinct physical names.
    return {
        alias: "eval_commerce" if alias == "commerce" else f"ds_{identifier + 800}"
        for alias, identifier in assets.datasource_ids.items()
    }


def read_env(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def write_bindings(path: Path, bindings: dict[str, Any]) -> None:
    lines = [line for line in read_env(path) if line.split("=", 1)[0] not in BINDING_KEYS]
    lines += [f"{key}='{json.dumps(value, ensure_ascii=False)}'" for key, value in bindings.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def already_provisioned(path: Path) -> bool:
    for line in read_env(path):
        key, _, value = line.partition("=")
        if key == "EVAL_DATASOURCE_IDS" and value.strip("'\" ") not in {"", "{}"}:
            return True
    return False


def load_fixtures(assets: EvalAssets, container: str, reader_password: str) -> dict[str, str]:
    pg = ImportAdmin(container)
    schemas = physical_names(assets)
    loaded = "eval_commerce" in pg.run("SELECT nspname FROM pg_namespace;")
    if not loaded:
        load_database(pg, assets.fixtures, check_manifest(assets.fixtures))
    statements = [
        "BEGIN;",
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replay_reader') THEN "
        "CREATE ROLE replay_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF; END $$;",
        f"ALTER ROLE replay_reader PASSWORD '{reader_password}';",
        "GRANT CONNECT ON DATABASE askdata_import TO replay_reader;",
    ]
    for alias, physical in schemas.items():
        source = assets.fixtures["datasources"][alias]["schema"]
        if not loaded:
            statements.append(f'ALTER SCHEMA "{source}" RENAME TO "{physical}";')
        statements += [
            f'GRANT USAGE ON SCHEMA "{physical}" TO replay_reader;',
            f'GRANT SELECT ON ALL TABLES IN SCHEMA "{physical}" TO replay_reader;',
        ]
    statements.append("COMMIT;")
    pg.run("\n".join(statements))
    return schemas


def provision_org(
    admin: Api, admin_id: int
) -> tuple[dict[str, int], dict[str, int], dict[str, Api]]:
    existing_teams = {
        t["name"]: t["id"] for t in admin.call("GET", "/api/org/teams?page_size=100")["items"]
    }
    teams = {
        alias: existing_teams.get(f"eval_{alias}")
        or admin.call("POST", "/api/org/teams", {"name": f"eval_{alias}"})["id"]
        for alias in ("sales", "finance")
    }
    existing_users = {
        u["username"]: u for u in admin.call("GET", "/api/org/users?page_size=100")["items"]
    }
    users: dict[str, int] = {"admin": admin_id}
    sessions: dict[str, Api] = {"admin": admin}
    for name, role, team in (
        ("sales_leader", "leader", "sales"),
        ("sales_member", "member", "sales"),
        ("finance_leader", "leader", "finance"),
        ("finance_member", "member", "finance"),
    ):
        password = secrets.token_urlsafe(24)
        found = existing_users.get(f"eval_{name}")
        if found:
            # Passwords are never stored by this script: rotate to a fresh one-time value.
            body = {"password": password, "role": role, "team_id": teams[team], "status": "active"}
            admin.call("PUT", f"/api/org/users/{found['id']}", body)
            users[name] = found["id"]
        else:
            created = admin.call(
                "POST",
                "/api/org/users",
                {
                    "username": f"eval_{name}",
                    "password": password,
                    "role": role,
                    "team_id": teams[team],
                },
            )
            users[name] = created["id"]
        sessions[name] = admin.login(f"eval_{name}", password)
    return teams, users, sessions


def provision_datasources(
    admin: Api, assets: EvalAssets, schemas: dict[str, str], reader_password: str
) -> dict[str, int]:
    ids: dict[str, int] = {}
    existing = {
        d["name"]: d["id"] for d in admin.call("GET", "/api/datasources?page_size=100")["items"]
    }
    for alias in assets.datasource_ids:
        body = {
            "name": f"eval-{alias}",
            "type": "postgresql",
            "host": "pg-import",
            "port": 5432,
            "database": "askdata_import",
            "username": "replay_reader",
            "password": reader_password,
            "schema_name": schemas[alias],
            "tls": False,
        }
        found = existing.get(f"eval-{alias}")
        created = (
            admin.call("PUT", f"/api/datasources/{found}", body)
            if found
            else admin.call("POST", "/api/datasources", body)
        )
        ids[alias] = created["id"]
        wait_task(admin, admin.call("POST", f"/api/schema/{created['id']}/sync")["id"])
    return ids


def apply_grants(admin: Api, assets: EvalAssets, teams: dict[str, int], ds: dict[str, int]) -> None:
    for team_alias, team_id in teams.items():
        for ds_alias, tables in assets.fixtures["grants"][team_alias].items():
            catalog = {
                t["name"]: t for t in admin.call("GET", f"/api/schema/{ds[ds_alias]}/tables")
            }
            writes = []
            for table_name, spec in tables.items():
                table = catalog[table_name]
                members = set(spec.get("member_columns") or [])
                masks = spec.get("masks") or {}
                writes.append(
                    {
                        "table_id": table["id"],
                        "member_visible": bool(members),
                        "columns": [
                            {
                                "column_id": column["id"],
                                "member_visible": column["name"] in members,
                                "mask_rule": (
                                    {"kind": MASK_KINDS[masks[column["name"]]]}
                                    if column["name"] in masks
                                    else None
                                ),
                            }
                            for column in table["columns"]
                            if column["name"] in spec["leader_columns"]
                        ],
                    }
                )
            admin.call(
                "PUT",
                f"/api/permissions/{ds[ds_alias]}/teams/{team_id}",
                {"tables": writes},
            )


def provision_semantics(
    admin: Api,
    sessions: dict[str, Api],
    assets: EvalAssets,
    ds: dict[str, int],
    physical: str,
) -> tuple[dict[str, str], dict[str, str]]:
    columns = {
        (t["name"], c["name"]): c["id"]
        for t in admin.call("GET", f"/api/schema/{ds['commerce']}/tables")
        for c in t["columns"]
    }
    semantic_aliases: dict[str, str] = {}
    known_semantics = {
        (s["target_type"], s["target_id"]): s
        for s in admin.call("GET", f"/api/semantic?datasource_id={ds['commerce']}")
    }
    for semantic in assets.fixtures["semantics"]:
        table_name, column_name = str(semantic["target"]).split(".")
        column_id = columns[table_name, column_name]
        item = known_semantics.get(("column", column_id)) or admin.call(
            "POST",
            "/api/semantic",
            {
                "datasource_id": ds["commerce"],
                "scope": "platform",
                "target_type": "column",
                "target_id": column_id,
                "aliases": [str(a) for a in semantic["aliases"]],
                "description": str(semantic["definition"]),
            },
        )
        # Retrieval keys are semantic:<target_type>:<column_id>; the adapter also
        # resolves semantic:<entry id>. Register both spellings.
        semantic_aliases[f"semantic:column:{column_id}"] = str(semantic["id"])
        semantic_aliases[f"semantic:{item['id']}"] = str(semantic["id"])

    owners = {"platform": "admin", "team": "sales_leader", "personal": "sales_member"}
    metric_aliases: dict[str, str] = {}
    known_metrics = {
        m["name"]: m
        for session in {id(v): v for v in sessions.values()}.values()
        for m in session.call("GET", f"/api/metric?datasource_id={ds['commerce']}")
    }
    for metric in assets.fixtures["metrics"]:
        item = known_metrics.get(str(metric["name"])) or sessions[
            owners[str(metric["scope"])]
        ].call(
            "POST",
            "/api/metric",
            {
                "datasource_id": ds["commerce"],
                "scope": str(metric["scope"]),
                "name": str(metric["name"]),
                "description": str(metric["definition"]),
                "sql_template": str(metric["sql_template"]).replace("commerce.", f"{physical}."),
            },
        )
        metric_aliases[f"metric:{item['id']}"] = str(metric["id"])
    return semantic_aliases, metric_aliases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", type=Path, default=ROOT / "deploy" / ".env")
    parser.add_argument("--import-container", default="askdata-pg-import-1")
    args = parser.parse_args()
    username = os.environ.get("ASKDATA_PROVISION_USERNAME")
    password = os.environ.get("ASKDATA_PROVISION_PASSWORD")
    if not username or not password:
        raise SystemExit("ASKDATA_PROVISION_USERNAME and ASKDATA_PROVISION_PASSWORD are required")
    if already_provisioned(args.env_file):
        raise SystemExit("evaluation fixtures are already provisioned for this env file")

    assets = EvalAssets(ROOT / "eval")
    admin = Api(args.api).login(username, password)
    me = admin.call("GET", "/api/auth/me")
    if me["role"] != "admin":
        raise SystemExit("the provisioning account must be an admin")

    reader_password = secrets.token_urlsafe(24)
    schemas = load_fixtures(assets, args.import_container, reader_password)
    teams, users, sessions = provision_org(admin, me["id"])
    datasources = provision_datasources(admin, assets, schemas, reader_password)
    apply_grants(admin, assets, teams, datasources)
    semantic_aliases, metric_aliases = provision_semantics(
        admin, sessions, assets, datasources, schemas["commerce"]
    )
    admin.call("POST", "/api/eval/cases/load")
    write_bindings(
        args.env_file,
        {
            "EVAL_TEAM_IDS": teams,
            "EVAL_USER_IDS": users,
            "EVAL_DATASOURCE_IDS": datasources,
            "EVAL_SEMANTIC_ALIASES": semantic_aliases,
            "EVAL_METRIC_ALIASES": metric_aliases,
        },
    )
    sys.stdout.write(
        json.dumps(
            {
                "teams": len(teams),
                "users": len(users),
                "datasources": len(datasources),
                "semantics": len(semantic_aliases),
                "metrics": len(metric_aliases),
                "env_file": str(args.env_file),
                "next": "docker compose ... up -d api worker",
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
