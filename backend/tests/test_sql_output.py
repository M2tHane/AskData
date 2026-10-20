"""The model's SQL answer survives sloppy display fields; the SQL itself is never altered."""

import json

import pytest
from pydantic import ValidationError

from app.core.prompt import PromptId, PromptRegistry
from app.modules.chat.stages import SqlOutput


def test_overlong_or_mistyped_display_fields_keep_the_sql():
    raw = {
        "sql": "SELECT 1 AS n",
        "title": "标题" * 200,
        "interpretation": "解释" * 800,
        "assumption": ["按客户所在地区"],
        "filters": "订单状态为已支付",
        "chart": "bar",
    }
    output = SqlOutput.model_validate_json(json.dumps(raw))
    assert output.sql == "SELECT 1 AS n"
    assert len(output.title) == 200 and len(output.interpretation) == 1000
    assert output.assumption == "['按客户所在地区']"
    assert output.filters == ["订单状态为已支付"]
    assert output.chart is None


def test_too_many_filters_are_cut_and_nulls_become_empty():
    output = SqlOutput.model_validate_json(
        json.dumps({"sql": None, "filters": [f"条件{i}" for i in range(80)] + [None]})
    )
    assert output.sql == "" and len(output.filters) == 50


def test_a_malformed_answer_still_fails():
    with pytest.raises(ValidationError):
        SqlOutput.model_validate_json('{"sql": "SELECT 1", "unanswerable": "maybe"}')


def test_sql_prompt_names_hierarchy_levels_and_length_limits():
    version, _, text = PromptRegistry().seed(PromptId.SQL)
    assert version == "v10"
    assert "Hierarchy levels" in text and "parent_id IS NOT NULL" in text
    assert "at most 100 characters" in text and "at most 8 short items" in text
    assert "never write date arithmetic" in text and "do not switch the date column" in text
    assert "按下单日期" in text
    assert "LEFT JOIN's ON clause removes no rows" in text
    assert "must sit at different levels" in text and "generate_series" in text
