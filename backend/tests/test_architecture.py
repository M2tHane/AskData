import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.architecture_rules import scan, violations

ROOT = Path(__file__).resolve().parents[1]


def test_application_architecture():
    assert scan(ROOT / "app") == {}


@pytest.mark.parametrize(
    "source,module",
    [
        ("import app.modules.auth.service", "app.core.security"),
        ("import asyncpg", "app.modules.chat.service"),
        ("import litellm", "app.modules.chat.service"),
        ("from app.modules.org.repository import Repo", "app.modules.auth.service"),
        ("from ..org.models import User", "app.modules.auth.service"),
        ("from app.modules.auth.repository import Repo", "app.modules.auth.api"),
        ("from app.core.datasource import PostgresConnector", "app.modules.chat.service"),
        ("raise ValueError('oops')", "app.modules.chat.service"),
        ("print('secret')", "app.core.security"),
        ("import os", "app.modules.modelcfg.service"),
        ("exec('SELECT')", "app.modules.chat.service"),
    ],
)
def test_architecture_rules_reject_real_bypasses(source, module):
    assert violations(source, module)


@pytest.mark.parametrize(
    "importer,dependency",
    [
        ("core/security.py", "fixtureapp.modules.auth.service"),
        ("modules/auth/service.py", "asyncpg"),
        ("modules/auth/repository.py", "fixtureapp.modules.auth.service"),
        ("modules/auth/api.py", "fixtureapp.modules.auth.repository"),
        ("modules/auth/service.py", "fixtureapp.modules.org.repository"),
    ],
)
def test_importlinter_contracts_reject_future_business_bypasses(tmp_path, importer, dependency):
    package = tmp_path / "fixtureapp"
    files = [
        "__init__.py",
        "main.py",
        "worker.py",
        "core/__init__.py",
        "core/security.py",
        "modules/__init__.py",
    ]
    for module in ("auth", "org"):
        files += [
            f"modules/{module}/{name}.py"
            for name in ("__init__", "api", "service", "repository", "models")
        ]
    for name in files:
        file = package / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("")
    (package / importer).write_text(f"import {dependency}\n")
    config = tmp_path / "importlinter.ini"
    config.write_text((ROOT / "importlinter.ini").read_text().replace("app", "fixtureapp"))
    result = subprocess.run(
        [str(Path(sys.executable).parent / "lint-imports"), "--config", str(config)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, result.stdout
    assert "BROKEN" in result.stdout, result.stdout + result.stderr
