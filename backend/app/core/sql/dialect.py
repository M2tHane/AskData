from enum import StrEnum

from sqlglot import exp


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
        if self.dialect == Dialect.MYSQL:
            return "MySQL: backtick identifiers; DATE_FORMAT for dates; LIMIT/OFFSET pagination."
        return (
            "PostgreSQL: double-quoted identifiers; DATE_TRUNC for dates; LIMIT/OFFSET pagination."
        )
