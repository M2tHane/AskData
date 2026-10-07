"""Regression checks for reference transport, scope consistency, and result comparison."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

# eval is an asset directory, not an importable business module.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_assets  # noqa: E402
from validate_cases import (  # noqa: E402
    Case,
    CheckError,
    Postgres,
    check_fixtures,
    check_manifest,
    check_results,
    compare_values,
    load_yaml,
    read_cases,
    reference_sql,
)


@pytest.fixture
def fixtures() -> dict[str, Any]:
    return load_yaml(ROOT / "fixtures.yaml")


def test_dataset_contract(fixtures: dict[str, Any]) -> None:
    check_fixtures(fixtures)
    cases = read_cases(fixtures)
    check_manifest(fixtures)
    assert len(cases) == 100
    assert sum(c.expect_status == "blocked" for c in cases) == 22


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT cost FROM commerce.product",
        "SELECT * FROM commerce.product",
        "SELECT sku FROM commerce.product WHERE cost>100",
        "SELECT p.sku FROM commerce.product p ORDER BY p.cost",
        "SELECT p.sku FROM commerce.product p JOIN commerce.orders o ON o.amt_net=p.cost",
        "SELECT x.price2 FROM (SELECT cost AS price2 FROM commerce.product) x",
        "WITH x AS (SELECT cost AS price2 FROM commerce.product) SELECT price2 FROM x",
        "SELECT employee_id FROM commerce.employee_salary",
    ],
)
def test_scope_checks_all_column_locations(sql: str, fixtures: dict[str, Any]) -> None:
    with pytest.raises(CheckError, match="outside fixture scope"):
        reference_sql(sql, "commerce", "sales_member", fixtures)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DELETE FROM commerce.orders",
        "WITH x AS (DELETE FROM commerce.orders RETURNING id) SELECT id FROM x",
        "SELECT id FROM commerce.orders FOR UPDATE",
        "SELECT id INTO copied_orders FROM commerce.orders",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT relname FROM pg_catalog.pg_class",
    ],
)
def test_reference_transport_rejects_unsafe_sql(sql: str, fixtures: dict[str, Any]) -> None:
    with pytest.raises(CheckError):
        reference_sql(sql, "commerce", "admin", fixtures)


def test_leader_and_member_have_different_scopes(fixtures: dict[str, Any]) -> None:
    sql = "SELECT internal_note FROM commerce.orders"
    reference_sql(sql, "commerce", "sales_leader", fixtures)
    with pytest.raises(CheckError):
        reference_sql(sql, "commerce", "sales_member", fixtures)
    # Finance leader has an explicitly authorized salary table; finance member does not.
    reference_sql(
        "SELECT base_salary FROM commerce.employee_salary", "commerce", "finance_leader", fixtures
    )
    with pytest.raises(CheckError):
        reference_sql(
            "SELECT base_salary FROM commerce.employee_salary",
            "commerce",
            "finance_member",
            fixtures,
        )


def test_scope_fixture_rejects_expansion(fixtures: dict[str, Any]) -> None:
    fixtures["grants"]["sales"]["commerce"]["product"]["member_columns"].append("cost")
    with pytest.raises(CheckError, match="subset"):
        check_fixtures(fixtures)


@pytest.mark.parametrize(
    ("actual", "expected", "mode", "matches"),
    [
        ([["b", "2"], ["a", "1"]], [["a", "1"], ["b", "2"]], "unordered", True),
        ([["b", "2"], ["a", "1"]], [["a", "1"], ["b", "2"]], "ordered", False),
        ([["2", "b"], ["1", "a"]], [["a", "1"], ["b", "2"]], "unordered", True),
        ([["a", "1"], ["2", "b"]], [["a", "1"], ["b", "2"]], "unordered", False),
        ([["a"], ["a"]], [["a"], ["b"]], "unordered", False),
        ([["0.000001"]], [["0"]], "scalar", True),
        ([["0.0000011"]], [["0"]], "scalar", False),
        ([[None]], [[""]], "scalar", False),
        ([["00123"]], [["123"]], "scalar", False),
        ([["NaN"]], [["0"]], "scalar", False),
        ([], [], "unordered", True),
        # Greedy matching would fail: match 0 with -1e-6, then 1e-6 with 0.
        ([["0"], ["0.000001"]], [["0"], ["-0.000001"]], "unordered", True),
    ],
)
def test_result_comparison(
    actual: list[list[str | None]], expected: list[list[str | None]], mode: str, matches: bool
) -> None:
    assert compare_values(actual, expected, mode) == matches


def test_psql_uses_read_only_transaction_and_role(monkeypatch: pytest.MonkeyPatch) -> None:
    statements: list[str] = []

    def fake_run(self: Postgres, sql: str) -> str:
        statements.append(sql)
        return "duplicate,duplicate\n1,__ASKDATA_NULL__\n"

    monkeypatch.setattr(Postgres, "run", fake_run)
    assert Postgres().query("SELECT 1,NULL", "sales_member") == [["1", None]]
    assert "BEGIN READ ONLY" in statements[0]
    assert 'SET LOCAL ROLE "eval_ref_sales_member"' in statements[0]
    assert "ROLLBACK" in statements[0]


def test_safety_and_clarify_cases_never_execute(
    fixtures: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_query(self: Postgres, sql: str, user: str) -> list[list[str | None]]:
        pytest.fail("non-executable case reached PostgreSQL")

    monkeypatch.setattr(Postgres, "query", forbidden_query)
    cases = [c for c in read_cases(fixtures) if c.expect_status != "success"]
    assert check_results(Postgres(), cases, fixtures) == {
        "executed": 0,
        "intentionally_empty": 0,
        "non_executable": 28,
    }


def test_unexpected_empty_reference_fails(
    fixtures: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def empty_query(self: Postgres, sql: str, user: str) -> list[list[str | None]]:
        return []

    monkeypatch.setattr(Postgres, "query", empty_query)
    with pytest.raises(CheckError, match="unexpected empty"):
        check_results(Postgres(), [read_cases(fixtures)[0]], fixtures)


def test_case_rejects_executable_safety_answer(fixtures: dict[str, Any]) -> None:
    case = next(c for c in read_cases(fixtures) if c.expect_status == "blocked")
    value = case.model_dump()
    value["expected_sql"] = "SELECT 1"
    with pytest.raises(ValueError, match="must not have executable"):
        Case.model_validate(value)


def test_rebuilt_assets_are_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_assets, "ROOT", tmp_path)

    def snapshot() -> dict[str, str]:
        return {
            str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tmp_path.rglob("*")
            if p.is_file()
        }

    build_assets.build_files()
    build_assets.build_cases()
    first = snapshot()
    build_assets.build_files()
    build_assets.build_cases()
    assert snapshot() == first
    assert all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
        for name, digest in first.items()
    )
