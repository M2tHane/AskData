from enum import StrEnum

from sqlglot import exp

from app.core.prompt import dialect_rules


class Dialect(StrEnum):
    MYSQL = "mysql"
    POSTGRES = "postgres"
    KINGBASE = "kingbase"
    GAUSSDB = "gaussdb"
    CLICKHOUSE = "clickhouse"

    @classmethod
    def from_name(cls, name: str) -> "Dialect":
        return cls("postgres" if name in {"postgresql", "upload"} else name)

    @property
    def sqlglot(self) -> str:
        """Map dialect to sqlglot dialect name for parsing/formatting."""
        mapping = {
            Dialect.MYSQL: "mysql",
            Dialect.POSTGRES: "postgres",
            Dialect.KINGBASE: "postgres",
            Dialect.GAUSSDB: "postgres",
            Dialect.CLICKHOUSE: "clickhouse",
        }
        return mapping[self]


class DialectAdapter:
    def __init__(self, dialect: Dialect) -> None:
        self.dialect = dialect

    def quote(self, identifier: str) -> str:
        return exp.to_identifier(identifier, quoted=True).sql(dialect=self.dialect.sqlglot)

    def paginate(self, query: exp.Query, limit: int, offset: int = 0) -> str:
        if limit < 1 or offset < 0:
            raise ValueError("Invalid pagination")
        return query.copy().limit(limit).offset(offset).sql(dialect=self.dialect.sqlglot)

    def date_trunc(self, unit: str, column: str) -> str:
        if unit not in {"day", "month", "year"}:
            raise ValueError("Unsupported date unit")
        expression = exp.DateTrunc(unit=exp.Literal.string(unit), this=exp.column(column))  # type: ignore[no-untyped-call]
        return expression.sql(dialect=self.dialect.sqlglot)

    def type_name(self, generic: str) -> str:
        if self.dialect == Dialect.MYSQL:
            types = {
                "integer": "BIGINT",
                "number": "DOUBLE",
                "text": "TEXT",
                "boolean": "BOOLEAN",
                "date": "DATE",
                "datetime": "DATETIME",
            }
        elif self.dialect == Dialect.CLICKHOUSE:
            types = {
                "integer": "Int64",
                "number": "Float64",
                "text": "String",
                "boolean": "UInt8",
                "date": "Date",
                "datetime": "DateTime",
            }
        else:  # postgres, kingbase, gaussdb
            types = {
                "integer": "BIGINT",
                "number": "DOUBLE PRECISION",
                "text": "TEXT",
                "boolean": "BOOLEAN",
                "date": "DATE",
                "datetime": "TIMESTAMPTZ",
            }
        return types[generic]

    @property
    def prompt_name(self) -> str:
        mapping = {
            Dialect.MYSQL: "mysql",
            Dialect.POSTGRES: "postgresql",
            Dialect.KINGBASE: "kingbase",
            Dialect.GAUSSDB: "gaussdb",
            Dialect.CLICKHOUSE: "clickhouse",
        }
        return mapping[self.dialect]

    @property
    def prompt_hint(self) -> str:
        """Rendered dialect guidance from the registered core dialect assets."""
        return dialect_rules(self.prompt_name)

    @property
    def engine_name(self) -> str:
        mapping = {
            Dialect.MYSQL: "MySQL",
            Dialect.POSTGRES: "PostgreSQL",
            Dialect.KINGBASE: "KingbaseES",
            Dialect.GAUSSDB: "GaussDB",
            Dialect.CLICKHOUSE: "ClickHouse",
        }
        return mapping[self.dialect]
