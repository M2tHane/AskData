import ast
import importlib.util
from pathlib import Path

# Driver/SDK imports are also checked by import-linter. AST rules cover constraints
# a module dependency graph cannot express (symbols, calls, raises and write routes).
BANNED = {
    "asyncpg",
    "aiomysql",
    "asyncmy",
    "psycopg",
    "psycopg2",
    "pymysql",
    "sqlite3",
    "litellm",
    "openai",
    "anthropic",
    "boto3",
    "minio",
    "redis",
    "arq",
    "sqlglot",
    "os",
    "logging",
    "structlog",
}
LOW_LEVEL = {
    "Connector",
    "QuerySession",
    "ConnectorRegistry",
    "PostgresConnector",
    "MysqlConnector",
}


def violations(source: str, module: str) -> list[str]:
    tree = ast.parse(source)
    issues = []
    business = module.startswith("app.modules.")
    owner = module.split(".")[2] if business else None
    layer = module.split(".")[3] if business and len(module.split(".")) > 3 else None
    imported_errors = {
        "AppError",
        "AuthenticationError",
        "PermissionDenied",
        "SqlBlocked",
        "DatasourceUnavailable",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                target = node.module or ""
                if node.level:
                    package = module.rpartition(".")[0]
                    target = importlib.util.resolve_name("." * node.level + target, package)
                targets = [target + "." + alias.name for alias in node.names]
                if target == "app.core.errors":
                    imported_errors.update(alias.asname or alias.name for alias in node.names)
                if (
                    business
                    and target == "app.core.datasource"
                    and any(alias.name in LOW_LEVEL for alias in node.names)
                ):
                    issues.append("SQL must use guard/executor; connectors are private")
            for target in targets:
                parts = target.split(".")
                if module.startswith("app.core") and target.startswith("app.modules"):
                    issues.append("core depends on business")
                if business and parts[0] in BANNED:
                    issues.append("external access bypasses core")
                if business and parts[0] == "sqlalchemy" and layer not in {"models", "repository"}:
                    issues.append("ORM outside repository/models")
                if business and target.startswith("app.modules.") and len(parts) >= 4:
                    if parts[2] != owner and parts[3] != "service":
                        issues.append("cross-module access must use service")
                    if parts[3] == "repository" and layer != "service":
                        issues.append("only service imports its repository")
                    if layer == "api" and parts[3] == "models":
                        issues.append("API imports database models")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                issues.append("print is forbidden")
            if (
                business
                and isinstance(node.func, ast.Name)
                and node.func.id in {"eval", "exec", "__import__"}
            ):
                issues.append("dynamic execution/import is forbidden")
            if (
                business
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
            ):
                issues.append("dynamic imports are forbidden")
        if business and isinstance(node, ast.Raise) and node.exc is not None:
            error = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if not isinstance(error, ast.Name) or error.id not in imported_errors:
                issues.append("business only raises registered AppError")
        if (
            business
            and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and layer == "api"
        ):
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in {"post", "put", "patch", "delete"}
                ):
                    service_calls = [
                        call
                        for call in ast.walk(node)
                        if isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and "service" in ast.unparse(call.func.value).lower()
                    ]
                    if not service_calls:
                        issues.append("write API must call an audited service")
    return issues


def scan(directory: Path) -> dict[str, list[str]]:
    failures = {}
    for file in directory.rglob("*.py"):
        relative = file.relative_to(directory).with_suffix("")
        parts = relative.parts
        module = (
            ".".join(("app", *parts[:-1])) if parts[-1] == "__init__" else ".".join(("app", *parts))
        )
        found = violations(file.read_text(), module)
        if found:
            failures[str(file)] = found
    return failures
