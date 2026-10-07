import pytest

from app.core.deps import CurrentUser, Role
from app.core.masking import MaskKind, MaskRule
from app.core.permission import TableScope, VisibleScope
from app.core.sql.guard import SqlGuard


def pytest_addoption(parser):
    parser.addoption(
        "--external-services",
        action="store_true",
        default=False,
        help="Run disposable Docker PostgreSQL/MySQL/Redis/MinIO integration tests",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "external: requires disposable Docker services")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--external-services"):
        for item in items:
            if "external" in item.keywords:
                item.add_marker(
                    pytest.mark.skip(reason="Use --external-services for Docker adapters")
                )


@pytest.fixture
def scope() -> VisibleScope:
    return VisibleScope(
        datasource_id=1,
        tables={
            "orders": TableScope(
                all_columns={
                    "id": "BIGINT",
                    "customer_id": "BIGINT",
                    "amount": "DECIMAL",
                    "phone": "TEXT",
                    "tenant_id": "BIGINT",
                    "secret": "TEXT",
                },
                columns={"id", "customer_id", "amount", "phone", "tenant_id"},
                masks={"phone": MaskRule(kind=MaskKind.PHONE)},
            ),
            "customers": TableScope(
                all_columns={"id": "BIGINT", "name": "TEXT"}, columns={"id", "name"}
            ),
        },
    )


@pytest.fixture
def guard() -> SqlGuard:
    return SqlGuard()


@pytest.fixture
def member() -> CurrentUser:
    return CurrentUser(id=3, role=Role.MEMBER, team_id=1)
