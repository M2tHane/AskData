"""Provision the cross-domain (hospital) evaluation fixtures on a running Compose stack.

Same path as scripts/provision_eval.py, for eval/crossdomain/: public REST APIs only, except
loading the synthetic business database into pg-import through `docker exec` as its local
administrator. Run from backend with the stack up:

    ASKDATA_PROVISION_USERNAME=admin ASKDATA_PROVISION_PASSWORD=... \
        uv run python scripts/provision_crossdomain.py

1. Loads eval/crossdomain/hospital.sql into askdata_import, renames schema hosp -> eval_hosp,
   and creates a SELECT-only `xd_reader` role limited to that schema (the existing
   replay_reader and the commerce fixtures are not touched).
2. As the admin: creates teams xd_clinical / xd_finance with members xd_clin_member /
   xd_fin_member, registers datasource "xd-hospital" with require_semantics (the admin UI's
   setup flow), syncs it, fills the semantics of columns without a comment and publishes every
   table, applies both teams' grants, creates the platform glossary items and metrics, and
   rebuilds the vector index.
2b. Imports eval/crossdomain/files/ through the real upload API (preview -> confirm, a second
   workbook sheet via add_sheet, an append), one upload datasource per alias owned by the
   team in files/manifest.json, and rebuilds their vector indexes.
3. Writes the resulting ids (no secrets) to eval/crossdomain/bindings.json for the runner.

The reader and member passwords are random, used once and never written anywhere.

Undo (ids are in eval/crossdomain/bindings.json):
  - API as admin: DELETE /api/semantic/{id} and /api/metric/{id} for the datasource's items,
    PATCH /api/datasources/{id}/status {"status": "disabled"} (there is no delete endpoint),
    the same for the five "xd-上传-*" upload datasources,
    PUT /api/org/users/{member} {"status": "disabled"} for both members;
    teams have no delete endpoint.
  - pg-import as import_admin: DROP SCHEMA eval_hosp CASCADE;
    REVOKE CONNECT ON DATABASE askdata_import FROM xd_reader; DROP ROLE xd_reader;
    the upload tables live in schemas ds_<datasource id> (DROP SCHEMA ds_<id> CASCADE).
"""

from __future__ import annotations

import base64
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

import yaml

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "eval" / "crossdomain"
PHYSICAL = "eval_hosp"
CONTAINER = "askdata-pg-import-1"


def psql(sql: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            CONTAINER,
            "psql",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-P",
            "format=csv",
            "-P",
            "footer=off",
            "-t",
            "-U",
            "import_admin",
            "-d",
            "askdata_import",
        ],
        input=sql,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode:
        raise SystemExit("PostgreSQL command failed (is the pg-import container running?)")
    return result.stdout


class Api:
    def __init__(self, base: str, token: str | None = None) -> None:
        self.base, self.token = base.rstrip("/"), token

    def call(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.base + path,
            method=method,
            headers=headers,
            data=None if body is None else json.dumps(body).encode(),
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.loads(response.read())["data"]
        except urllib.error.HTTPError as error:
            payload = json.loads(error.read() or b"{}")
            code, message = payload.get("code", error.code), payload.get("message", "")
            raise SystemExit(f"{method} {path} failed: {code} {message}") from None

    def login(self, username: str, password: str) -> Api:
        token = self.call("POST", "/api/auth/login", {"username": username, "password": password})
        return Api(self.base, token["access_token"])


def wait_task(admin: Api, task_id: str) -> None:
    for _ in range(600):
        task = admin.call("GET", f"/api/schema/tasks/{task_id}")
        if task["status"] == "success":
            return
        if task["status"] == "failed":
            raise SystemExit(f"task failed: {task.get('error')}")
        time.sleep(1)
    raise SystemExit("task timed out")


def wait_meta_task(task_id: str) -> None:
    """Index tasks have no status endpoint; read their row in the metadata database."""
    if not all(ch in "0123456789abcdef-" for ch in task_id):
        raise SystemExit("unexpected task id")
    for _ in range(900):
        status = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "askdata-pg-meta-1",
                "psql",
                "-X",
                "-q",
                "-t",
                "-A",
                "-U",
                "askdata",
                "-d",
                "askdata",
                "-c",
                f"SELECT status FROM task_run WHERE task_id = '{task_id}'",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout.strip()
        if status == "success":
            return
        if status == "failed":
            raise SystemExit("index rebuild failed")
        time.sleep(2)
    raise SystemExit("index rebuild timed out")


def load_database(reader_password: str) -> None:
    schemas = set(psql("SELECT nspname FROM pg_namespace;").split())
    if PHYSICAL not in schemas:
        if "hosp" in schemas:
            raise SystemExit("schema hosp already exists; drop it or rename it first")
        psql((ASSETS / "hospital.sql").read_text(encoding="utf-8"))
        psql(f'ALTER SCHEMA hosp RENAME TO "{PHYSICAL}";')
    psql(
        "BEGIN;\n"
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'xd_reader') THEN "
        "CREATE ROLE xd_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF; END $$;\n"
        f"ALTER ROLE xd_reader PASSWORD '{reader_password}';\n"
        "GRANT CONNECT ON DATABASE askdata_import TO xd_reader;\n"
        f'GRANT USAGE ON SCHEMA "{PHYSICAL}" TO xd_reader;\n'
        f'GRANT SELECT ON ALL TABLES IN SCHEMA "{PHYSICAL}" TO xd_reader;\n'
        "COMMIT;\n"
    )


def wait_upload(admin: Api, job_id: int) -> None:
    for _ in range(300):
        job = admin.call("GET", f"/api/upload/{job_id}")
        if job["status"] == "success":
            return
        if job["status"] == "failed":
            raise SystemExit(f"upload {job_id} failed: {job.get('error')}")
        time.sleep(1)
    raise SystemExit("upload timed out")


def upload_files(admin: Api, team_ids: dict[str, int]) -> dict[str, int]:
    """The real upload flow: preview -> confirm (overwrite), add_sheet, append; owner team
    gets every column on publish. One datasource per alias; existing ones are reused."""
    manifest = json.loads((ASSETS / "files" / "manifest.json").read_text(encoding="utf-8"))
    existing = {
        d["name"]: d["id"] for d in admin.call("GET", "/api/datasources?page_size=100")["items"]
    }
    ids: dict[str, int] = {}
    skip: set[str] = set()
    last_preview: dict[str, int] = {}
    pending: list[tuple[int, dict[str, Any], str]] = []

    def flush() -> None:
        # A workbook's sheets are previewed from the first job before any of them is confirmed.
        for job_id, config, mode in pending:
            job = admin.call(
                "POST", f"/api/upload/{job_id}/confirm", {"config": config, "mode": mode}
            )
            wait_upload(admin, job["id"])
        pending.clear()

    for step in manifest["imports"]:
        alias = step["alias"]
        if step["step"] == "preview" and step["datasource_name"] in existing:
            ids[alias] = existing[step["datasource_name"]]
            skip.add(alias)
        if alias in skip:
            continue
        if step["step"] == "add_sheet":
            preview = admin.call(
                "POST", f"/api/upload/{last_preview[alias]}/sheets", step["config"]
            )
        else:
            if step["step"] == "append":
                flush()
            preview = admin.call(
                "POST",
                "/api/upload/preview",
                {
                    "filename": step["file"],
                    "content_base64": base64.b64encode(
                        (ASSETS / "files" / step["file"]).read_bytes()
                    ).decode(),
                    "datasource_id": ids.get(alias) if step["step"] == "append" else None,
                    "datasource_name": step.get("datasource_name", alias),
                    "owner_team_id": team_ids[step["team"]],
                    "config": step["config"],
                },
            )
            last_preview[alias] = preview["id"]
        ids[alias] = preview["datasource_id"]
        pending.append(
            (preview["id"], step["config"], "append" if step["step"] == "append" else "overwrite")
        )
    flush()
    for identifier in ids.values():
        wait_meta_task(
            admin.call("POST", "/api/embedding/rebuild", {"datasource_id": identifier})["id"]
        )
    return ids


def main() -> int:
    username = os.environ.get("ASKDATA_PROVISION_USERNAME")
    password = os.environ.get("ASKDATA_PROVISION_PASSWORD")
    if not username or not password:
        raise SystemExit("ASKDATA_PROVISION_USERNAME and ASKDATA_PROVISION_PASSWORD are required")
    fixtures = yaml.safe_load((ASSETS / "fixtures.yaml").read_text(encoding="utf-8"))
    admin = Api(os.environ.get("ASKDATA_API", "http://127.0.0.1:8000")).login(username, password)
    me = admin.call("GET", "/api/auth/me")
    if me["role"] != "admin":
        raise SystemExit("the provisioning account must be an admin")

    reader_password = secrets.token_urlsafe(24)
    load_database(reader_password)

    # Organisation: two teams with one restricted member each. Admin = the existing admin.
    teams = {t["name"]: t["id"] for t in admin.call("GET", "/api/org/teams?page_size=100")["items"]}
    team_ids = {
        alias: teams.get(f"xd_{alias}")
        or admin.call("POST", "/api/org/teams", {"name": f"xd_{alias}"})["id"]
        for alias in ("clinical", "finance")
    }
    users = {u["username"]: u for u in admin.call("GET", "/api/org/users?page_size=100")["items"]}
    user_ids = {"admin": me["id"]}
    for alias, team in (("clin_member", "clinical"), ("fin_member", "finance")):
        body = {"password": secrets.token_urlsafe(24), "role": "member", "team_id": team_ids[team]}
        if f"xd_{alias}" in users:
            user_ids[alias] = users[f"xd_{alias}"]["id"]
            admin.call("PUT", f"/api/org/users/{user_ids[alias]}", {**body, "status": "active"})
        else:
            user_ids[alias] = admin.call(
                "POST", "/api/org/users", {"username": f"xd_{alias}", **body}
            )["id"]

    # Datasource through the setup flow: nothing is visible until every column has a semantic.
    body = {
        "name": "xd-hospital",
        "type": "postgresql",
        "host": "pg-import",
        "port": 5432,
        "database": "askdata_import",
        "username": "xd_reader",
        "password": reader_password,
        "schema_name": PHYSICAL,
        "tls": False,
        "require_semantics": True,
    }
    existing = {
        d["name"]: d["id"] for d in admin.call("GET", "/api/datasources?page_size=100")["items"]
    }
    ds_id = (
        admin.call("PUT", f"/api/datasources/{existing['xd-hospital']}", body)
        if "xd-hospital" in existing
        else admin.call("POST", "/api/datasources", body)
    )["id"]
    wait_task(admin, admin.call("POST", f"/api/schema/{ds_id}/sync")["id"])

    setup = admin.call("GET", f"/api/schema/{ds_id}/setup")
    described = fixtures["setup_descriptions"]
    admin.call(
        "PUT",
        f"/api/schema/{ds_id}/setup",
        {
            "publish": True,
            "tables": [
                {
                    "table_id": table["id"],
                    "include": True,
                    "columns": [
                        {
                            "column_id": column["id"],
                            "description": described.get(f"{table['name']}.{column['name']}")
                            or column["description"]
                            or column["comment"],
                        }
                        for column in table["columns"]
                    ],
                }
                for table in setup["tables"]
            ],
        },
    )

    catalog = {t["name"]: t for t in admin.call("GET", f"/api/schema/{ds_id}/tables")}
    if set(catalog) != set(fixtures["catalog"]["hospital"]):
        raise SystemExit("published tables differ from the fixture catalog")
    for team_alias, team_id in team_ids.items():
        writes = []
        for table_name, spec in fixtures["grants"][team_alias]["hospital"].items():
            members = set(spec.get("member_columns") or [])
            writes.append(
                {
                    "table_id": catalog[table_name]["id"],
                    "member_visible": bool(members),
                    "columns": [
                        {
                            "column_id": c["id"],
                            "member_visible": c["name"] in members,
                            "mask_rule": None,
                        }
                        for c in catalog[table_name]["columns"]
                        if c["name"] in spec["leader_columns"]
                    ],
                }
            )
        admin.call("PUT", f"/api/permissions/{ds_id}/teams/{team_id}", {"tables": writes})

    semantic_aliases: dict[str, str] = {}
    metric_aliases: dict[str, str] = {}
    if (ASSETS / "semantics_admin.yaml").exists():
        # The semantic layer was later configured independently by an administrator
        # (semantics_admin.yaml). Never recreate the original fixture items over it; bind the
        # live items instead (scoring ids sem_<id> / met_<id>).
        semantic_aliases = {
            f"semantic:{s['id']}": f"sem_{s['id']}"
            for s in admin.call("GET", f"/api/semantic?datasource_id={ds_id}")
        }
        metric_aliases = {
            f"metric:{m['id']}": f"met_{m['id']}"
            for m in admin.call("GET", f"/api/metric?datasource_id={ds_id}")
        }
    else:
        columns = {(t["name"], c["name"]): c["id"] for t in catalog.values() for c in t["columns"]}
        known = {
            (s["target_type"], s["target_id"]): s
            for s in admin.call("GET", f"/api/semantic?datasource_id={ds_id}")
        }
        for item in fixtures["semantics"]:
            table_name, column_name = str(item["target"]).split(".")
            column_id = columns[table_name, column_name]
            saved = known.get(("column", column_id)) or admin.call(
                "POST",
                "/api/semantic",
                {
                    "datasource_id": ds_id,
                    "scope": "platform",
                    "target_type": "column",
                    "target_id": column_id,
                    "aliases": [str(a) for a in item["aliases"]],
                    "description": str(item["definition"]),
                },
            )
            semantic_aliases[f"semantic:column:{column_id}"] = str(item["id"])
            semantic_aliases[f"semantic:{saved['id']}"] = str(item["id"])
        known_metrics = {
            m["name"]: m for m in admin.call("GET", f"/api/metric?datasource_id={ds_id}")
        }
        for item in fixtures["metrics"]:
            saved = known_metrics.get(str(item["name"])) or admin.call(
                "POST",
                "/api/metric",
                {
                    "datasource_id": ds_id,
                    "scope": "platform",
                    "name": str(item["name"]),
                    "description": str(item["definition"]),
                    "sql_template": str(item["sql_template"]).replace("hosp.", f"{PHYSICAL}."),
                },
            )
            metric_aliases[f"metric:{saved['id']}"] = str(item["id"])

    wait_meta_task(admin.call("POST", "/api/embedding/rebuild", {"datasource_id": ds_id})["id"])
    datasource_ids = {"hospital": ds_id, **upload_files(admin, team_ids)}

    bindings = {
        "EVAL_TEAM_IDS": team_ids,
        "EVAL_USER_IDS": user_ids,
        "EVAL_DATASOURCE_IDS": datasource_ids,
        "EVAL_SEMANTIC_ALIASES": semantic_aliases,
        "EVAL_METRIC_ALIASES": metric_aliases,
    }
    (ASSETS / "bindings.json").write_text(
        json.dumps(bindings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sys.stdout.write(
        json.dumps(
            {
                "datasources": datasource_ids,
                "teams": team_ids,
                "users": user_ids,
                "semantics": len(fixtures["semantics"]),
                "metrics": len(metric_aliases),
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
