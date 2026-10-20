import importlib.util
import json
import stat
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "provision_eval.py"


@pytest.fixture(scope="module")
def provision():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("provision_eval", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bindings_are_written_private_and_replace_previous_values(provision, tmp_path):  # type: ignore[no-untyped-def]
    env = tmp_path / ".env"
    env.write_text("JWT_SECRET=keep\nEVAL_TEAM_IDS='{}'\n", encoding="utf-8")
    provision.write_bindings(env, {"EVAL_TEAM_IDS": {"sales": 2}, "EVAL_USER_IDS": {"admin": 1}})
    lines = env.read_text(encoding="utf-8").splitlines()
    assert "JWT_SECRET=keep" in lines
    assert sum(line.startswith("EVAL_TEAM_IDS=") for line in lines) == 1
    assert json.loads(
        next(line for line in lines if line.startswith("EVAL_TEAM_IDS="))
        .split("=", 1)[1]
        .strip("'")
    ) == {"sales": 2}
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_already_provisioned_only_when_datasource_bindings_exist(provision, tmp_path):  # type: ignore[no-untyped-def]
    env = tmp_path / ".env"
    assert provision.already_provisioned(env) is False
    env.write_text("EVAL_DATASOURCE_IDS='{}'\n", encoding="utf-8")
    assert provision.already_provisioned(env) is False
    env.write_text("EVAL_DATASOURCE_IDS='{\"commerce\": 3}'\n", encoding="utf-8")
    assert provision.already_provisioned(env) is True


def test_physical_schema_names_are_distinct_and_keep_commerce(provision):  # type: ignore[no-untyped-def]
    class Assets:
        datasource_ids = {"commerce": 1, "upload_daily": 3}

    names = provision.physical_names(Assets())
    assert names == {"commerce": "eval_commerce", "upload_daily": "ds_803"}
