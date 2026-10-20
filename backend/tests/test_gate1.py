import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.core.datasource import ConnectorRegistry, Datasource
from app.core.errors import SqlBlocked
from app.core.llm import CallContext, ChatMessage, LLMClient, ModelConfig, ModelRegistry
from app.core.sql.dialect import Dialect
from app.core.sql.executor import QueryLimits, SqlExecutor
from tests.fakes import FakeConnector, FakeModels, FakeTransport, FakeUsage

CASES = json.loads((Path(__file__).parent / "fixtures/gate1.json").read_text())


def test_gate_category_counts():
    assert Counter(case["category"] for case in CASES) == {"违规": 10, "越权": 8, "注入": 4}


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{n}-{case['category']}" for n, case in enumerate(CASES)]
)
async def test_gate1_fixed_model_outputs_are_blocked_before_connection(case, guard, scope):
    client = LLMClient(
        ModelRegistry(
            FakeModels(
                [
                    ModelConfig(id="fixed", kind="llm", model="fake", api_key=SecretStr("fake")),
                ]
            )
        ),
        FakeUsage(),
        FakeTransport(case["sql"]),
    )
    response = await client.chat(
        [ChatMessage(role="user", content="fixture")],
        CallContext(user_id=3, team_id=1, prompt_version="sql_generation:v1"),
    )
    connector = FakeConnector()
    executor = SqlExecutor(ConnectorRegistry({Dialect.POSTGRES: connector}))
    with pytest.raises(SqlBlocked) as caught:
        checked = guard.check(response.text, "postgres", scope)
        await executor.run(
            Datasource(
                id=1,
                dialect=Dialect.POSTGRES,
                host="fake",
                port=5432,
                database="fake",
                username="fake",
                password=SecretStr("fake"),
            ),
            checked,
            QueryLimits(user_id=3),
        )
    assert caught.value.code == case["code"]
    assert connector.opened == 0 and not connector.executed and not connector.explained
    assert "employee_salary" not in caught.value.message and "secret" not in caught.value.message
