from enum import StrEnum

from sqlglot import exp

from app.core.prompt import dialect_rules


class Dialect(StrEnum):
    MYSQL = "mysql"
    POSTGRES = "postgres"

    @classmethod
    def from_name(cls, name: str) -> "Dialect":
        return cls("postgres" if name in {"postgresql", "upload"} else name)


class DialectAdapter:
    def __init__(self, dialect: Dialect) -> None:
        self.dialect = dialect

    def quote(self, identifier: str) -> str:
        return exp.to_identifier(identifier, quoted=True).sql(dialect=self.dialect)

    def paginate(self, query: exp.Query, limit: int, offset: int = 0) -> str:
        if limit < 1 or offset < 0:
            raise ValueError("Invalid pagination")
        return query.copy().limit(limit).offset(offset).sql(dialect=self.dialect)

    def date_trunc(self, unit: str, column: str) -> str:
        if unit not in {"day", "month", "year"}:
            raise ValueError("Unsupported date unit")
        expression = exp.DateTrunc(unit=exp.Literal.string(unit), this=exp.column(column))  # type: ignore[no-untyped-call]
        return expression.sql(dialect=self.dialect)

    def type_name(self, generic: str) -> str:
        types = {
            "integer": "BIGINT",
            "number": "DOUBLE" if self.dialect == Dialect.MYSQL else "DOUBLE PRECISION",
            "text": "TEXT",
            "boolean": "BOOLEAN",
            "date": "DATE",
            "datetime": "DATETIME" if self.dialect == Dialect.MYSQL else "TIMESTAMPTZ",
        }
        return types[generic]

    @property
    def prompt_hint(self) -> str:
        """Rendered dialect guidance from the registered core dialect assets."""
        return dialect_rules("mysql" if self.dialect == Dialect.MYSQL else "postgresql")

    @property
    def engine_name(self) -> str:
        return "MySQL" if self.dialect == Dialect.MYSQL else "PostgreSQL"
