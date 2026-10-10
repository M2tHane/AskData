"""Fail-closed AST authorization. Only this module may issue execution capabilities."""

import hashlib
import hmac
import re
import secrets
from typing import Any, cast

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SchemaError, SqlglotError
from sqlglot.lineage import lineage
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

from app.core.errors import ErrorCode, SqlBlocked, UnresolvedReference
from app.core.masking import MaskKind, MaskRule
from app.core.permission import RowFilter, RowOperator, VisibleScope
from app.core.sql.dialect import Dialect

_SYSTEM_SCHEMAS = {
    "information_schema",
    "pg_catalog",
    "mysql",
    "performance_schema",
    "sys",
    "system",
    "sys_catalog",  # Kingbase
}
_DANGEROUS = {
    "pg_sleep",
    "pg_sleep_for",
    "pg_sleep_until",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "dblink",
    "sleep",
    "benchmark",
    "load_file",
    "nextval",
    "setval",
    "set_config",
    "pg_notify",
    "get_lock",
    "release_lock",
    "sys_exec",
    "sys_eval",
    # ClickHouse table functions that reach outside the DB
    "url",
    "file",
    "s3",
    "hdfs",
    "remote",
    "remotesecure",
    "mysql",
    "postgresql",
    "jdbc",
    "odbc",
    "input",
    "cluster",
    "clusterallreplicas",
    "executable",
    "azureblobstorage",
    "gcs",
    "dictionary",
}
# Unknown UDFs are rejected: a SELECT can call a function with side effects.
_SAFE_FUNCTIONS = set(
    """
abs acos asin atan atan2 avg and or not exists case array_agg array_length array_size
array_contains array_concat
array_append array_slice cardinality cast try_cast ceil ceiling coalesce concat concat_ws
count cot cume_dist current_date current_time current_timestamp date date_add date_sub
 date_diff datediff date_trunc date_format day dayofmonth dayofweek dayofyear dense_rank
extract exp first_value floor greatest group_concat if ifnull isnull json_extract
json_extract_scalar jsonb_extract_scalar json_array_length jsonb_array_length last_value
lag lead least left length ln log log10 lower ltrim max min mod month now ntile nullif
percent_rank percentile_cont percentile_disc position pow power rank replace right round
row_number rtrim sign sin sqrt stddev stddev_pop stddev_samp str_to_date string_agg
substring sum tan time_to_str timestamp_add timestamp_diff timestamp_trunc to_char
trim trunc truncate upper variance var_pop var_samp week year unix_timestamp
str_to_time time_to_unix unix_to_time ts_or_ds_to_date ts_or_ds_add safe_divide
""".split()
)
# Pure ClickHouse functions (names as sqlglot reports them), accepted only for ClickHouse.
_CLICKHOUSE_SAFE_FUNCTIONS = set(
    """
approx_distinct count_if multiif today yesterday todate todatetime toyear tomonth toquarter
todayofmonth todayofweek tohour tostartofday tostartofweek tostartofmonth tostartofquarter
tostartofyear toyyyymm toyyyymmdd tostring tofloat64 toint64 touint64 todecimal64 todecimal128
uniq uniqexact argmax argmin any anylast median quantile quantileexact arg_max arg_min
array_join
""".split()
)
# Aggregate combinators (sumIf, avgIf, uniqIf...) are pure when their base aggregate is.
_CLICKHOUSE_COMBINABLE = {"sum", "avg", "min", "max", "count", "uniq", "uniqexact", "any"}
_SEAL_KEY = secrets.token_bytes(32)


class GuardedSql(BaseModel):
    model_config = ConfigDict(frozen=True)
    normalized_sql: str
    dialect: Dialect
    datasource_id: int
    tables: frozenset[str]
    columns: dict[str, frozenset[str]]
    output_masks: dict[str, MaskRule] = Field(default_factory=dict)
    seal: str = Field(repr=False)

    def authentic(self) -> bool:
        try:
            return hmac.compare_digest(self.seal, _signature(self.model_dump(exclude={"seal"})))
        except (ValueError, TypeError):
            return False


def _signature(payload: dict[str, Any]) -> str:
    # Pydantic's JSON conversion handles enums/frozensets deterministically via explicit sorting.
    import json

    def convert(value: Any) -> Any:
        if isinstance(value, (set, frozenset)):
            return sorted(value)
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        raise TypeError("Unsupported seal payload")

    serialized = json.dumps(payload, sort_keys=True, default=convert, separators=(",", ":"))
    return hmac.new(_SEAL_KEY, serialized.encode(), hashlib.sha256).hexdigest()


def _table_name(table: exp.Table) -> str:
    return ".".join(part.name for part in table.parts)


class SqlGuard:
    def check(self, sql: str, dialect: str | Dialect, scope: VisibleScope) -> GuardedSql:
        try:
            return self._check(sql, dialect, scope)
        except SqlBlocked:
            raise
        except (SqlglotError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise SqlBlocked(ErrorCode.SQL_PARSE_FAILED) from None

    def _check(self, sql: str, dialect: str | Dialect, scope: VisibleScope) -> GuardedSql:
        try:
            target = Dialect.from_name(dialect)
            statements = sqlglot.parse(
                sql, read=target.sqlglot, error_level=sqlglot.ErrorLevel.RAISE
            )
        except (ParseError, ValueError, RecursionError):
            raise SqlBlocked(ErrorCode.SQL_PARSE_FAILED) from None
        statements = [
            statement
            for statement in statements
            if statement is not None and not isinstance(statement, exp.Semicolon)
        ]
        if len(statements) > 1:
            raise SqlBlocked(ErrorCode.SQL_MULTI_STATEMENT)
        if not statements:
            raise SqlBlocked(ErrorCode.SQL_PARSE_FAILED)
        tree = statements[0]
        if not isinstance(tree, (exp.Select, exp.SetOperation)):
            raise SqlBlocked(ErrorCode.SQL_NOT_SELECT)
        for node in tree.walk():
            # ClickHouse SETTINGS/FORMAT clauses could lift server limits or change output.
            if isinstance(node, exp.Select) and (
                node.args.get("settings") or node.args.get("format")
            ):
                raise SqlBlocked(ErrorCode.SQL_FORBIDDEN_FUNC)
            if isinstance(node, (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Lock)):
                raise SqlBlocked(ErrorCode.SQL_NOT_SELECT)
            if isinstance(node, (exp.Placeholder, exp.Parameter, exp.SessionParameter)):
                raise SqlBlocked(ErrorCode.SQL_PARSE_FAILED)
            if isinstance(node, exp.With) and node.args.get("recursive"):
                raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
            if isinstance(node, exp.DataType) and node.this == exp.DataType.Type.USERDEFINED:
                raise SqlBlocked(ErrorCode.SQL_FORBIDDEN_FUNC)
            if isinstance(node, exp.Func):
                name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
                name = name.lower()
                if name in _DANGEROUS or name.startswith(("lo_", "dblink", "pg_")):
                    raise SqlBlocked(ErrorCode.SQL_FORBIDDEN_FUNC)
                if target == Dialect.CLICKHOUSE and isinstance(node, exp.CombinedAggFunc):
                    base = str(node.name).lower().removesuffix("if")
                    name = base if base in _CLICKHOUSE_COMBINABLE else name
                if name not in _SAFE_FUNCTIONS and not (
                    target == Dialect.CLICKHOUSE and name in _CLICKHOUSE_SAFE_FUNCTIONS
                ):
                    raise SqlBlocked(ErrorCode.SQL_FORBIDDEN_FUNC)
                if isinstance(node.parent, exp.Dot):
                    raise SqlBlocked(ErrorCode.SQL_FORBIDDEN_FUNC)
        normalize_identifiers(tree, dialect=target.sqlglot)
        schema = MappingSchema(dialect=target.sqlglot, normalize=False)
        for name, table_scope in scope.tables.items():
            schema.add_table(name, table_scope.all_columns, dialect=target.sqlglot)
        for query_scope in traverse_scope(tree):
            for source in query_scope.sources.values():
                if not isinstance(source, exp.Table):
                    continue
                name = _table_name(source)
                if any(part.name.lower() in _SYSTEM_SCHEMAS for part in source.parts[:-1]):
                    raise SqlBlocked(ErrorCode.SQL_SYSTEM_TABLE)
                # PG catalog objects can be reached without a schema through search_path.
                if source.name.lower().startswith(("pg_", "sql_")):
                    raise SqlBlocked(ErrorCode.SQL_SYSTEM_TABLE)
                if source.catalog or not isinstance(source.this, exp.Identifier):
                    raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
                if name not in scope.tables:
                    raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
        try:
            tree = qualify(
                tree,
                dialect=target.sqlglot,
                schema=schema,
                infer_schema=False,
                validate_qualify_columns=True,
                allow_partial_qualification=False,
            )
        except (OptimizeError, SchemaError, ValueError, TypeError) as error:
            raise UnresolvedReference(_unresolved(str(error))) from None
        used: dict[str, set[str]] = {}
        tables: dict[int, tuple[exp.Table, str]] = {}
        for query_scope in traverse_scope(tree):
            for source in query_scope.sources.values():
                if isinstance(source, exp.Table):
                    name = _table_name(source)
                    if name not in scope.tables:
                        raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
                    tables[id(source)] = (source, name)
                    used.setdefault(name, set())
            for column in query_scope.columns:
                if column.is_star:
                    raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
                owner: Scope = query_scope
                resolved: Any = owner.sources.get(column.table)
                while resolved is None and owner.parent is not None:
                    owner = owner.parent
                    resolved = owner.sources.get(column.table)
                reference = f"{column.table}.{column.name}" if column.table else column.name
                if isinstance(resolved, exp.Table):
                    name = _table_name(resolved)
                    if (
                        column.name
                        in scope.tables[name].all_columns.keys() - scope.tables[name].columns
                    ):
                        # Withheld from this user: refuse outright, never offer a correction.
                        raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
                    if column.name not in scope.tables[name].columns:
                        raise UnresolvedReference(reference)
                    used.setdefault(name, set()).add(column.name)
                elif isinstance(resolved, Scope):
                    if column.name not in cast(exp.Query, resolved.expression).named_selects:
                        raise UnresolvedReference(reference)
                else:
                    raise UnresolvedReference(reference)
        # Unresolved stars in projections (e.g. unknown-schema derived tables) never pass.
        for select in tree.find_all(exp.Select):
            if any(projection.is_star for projection in select.selects):
                raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
        masks = self._output_masks(tree, scope, schema, target)
        for table, name in tables.values():
            filters = scope.tables[name].row_filters
            if not filters:
                continue
            alias = table.alias_or_name
            inner_table = table.copy()
            inner_table.set("alias", None)
            conditions = [
                self._row_condition(rule, scope.tables[name].all_columns) for rule in filters
            ]
            inner = exp.select(
                *[exp.column(column, quoted=True) for column in sorted(scope.tables[name].columns)]
            )
            inner = inner.from_(inner_table).where(exp.and_(*conditions))
            table.replace(inner.subquery(alias=exp.to_identifier(alias, quoted=True)))
        normalized = tree.sql(
            dialect=target.sqlglot, comments=False, unsupported_level=sqlglot.ErrorLevel.RAISE
        )
        payload: dict[str, Any] = {
            "normalized_sql": normalized,
            "dialect": target,
            "datasource_id": scope.datasource_id,
            "tables": frozenset(used),
            "columns": {name: frozenset(cols) for name, cols in used.items()},
            "output_masks": masks,
        }
        return GuardedSql(**payload, seal=_signature(payload))

    @staticmethod
    def _row_condition(rule: RowFilter, columns: dict[str, str]) -> exp.Expression:
        if rule.column not in columns:
            raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
        column = exp.column(rule.column, quoted=True)
        if rule.operator == RowOperator.IN:
            if not isinstance(rule.value, list) or not rule.value:
                raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
            return exp.In(this=column, expressions=[exp.convert(value) for value in rule.value])
        if isinstance(rule.value, list):
            raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
        if rule.value is None:
            condition: exp.Expression = exp.Is(this=column, expression=exp.Null())
            return exp.Not(this=condition) if rule.operator == RowOperator.NE else condition
        cls = exp.EQ if rule.operator == RowOperator.EQ else exp.NEQ
        return cls(this=column, expression=exp.convert(rule.value))

    @staticmethod
    def _output_masks(
        tree: exp.Query,
        scope: VisibleScope,
        schema: MappingSchema,
        dialect: Dialect,
    ) -> dict[str, MaskRule]:
        masks: dict[str, MaskRule] = {}
        names = tree.named_selects
        if len(names) != len(set(names)):
            raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE)
        for name in names:
            try:
                graph = lineage(
                    exp.column(name, quoted=True),
                    tree,
                    schema=schema,
                    dialect=dialect.sqlglot,
                )
            except (SqlglotError, ValueError):
                raise SqlBlocked(ErrorCode.SQL_OUT_OF_SCOPE) from None
            matched: list[MaskRule] = []
            for node in graph.walk():
                if isinstance(node.expression, exp.Table):
                    table_name = _table_name(node.expression)
                    # Leaf names are SQL identifiers, including quotes and alias qualification.
                    column = sqlglot.parse_one(node.name, read=dialect.sqlglot)
                    if isinstance(column, exp.Column) and table_name in scope.tables:
                        rule = scope.tables[table_name].masks.get(column.name)
                        if rule is not None:
                            matched.append(rule)
            if matched:
                projection = next((p for p in tree.selects if p.alias_or_name == name), None)
                # Any transformation of masked data gets a full mask; aliases preserve its rule.
                direct = projection is not None and isinstance(projection.unalias(), exp.Column)
                direct = direct and all(
                    isinstance(node.expression, exp.Table)
                    or isinstance(node.expression.unalias(), exp.Column)
                    for node in graph.walk()
                )
                masks[name] = (
                    matched[0] if direct and len(matched) == 1 else MaskRule(kind=MaskKind.FULL)
                )
        return masks


def _unresolved(message: str) -> str:
    """The identifier sqlglot could not resolve, without the rest of its message."""
    match = re.search(r"Column '([^']+)' could not be resolved|Unknown column: (\S+)", message)
    return (match[1] or match[2]).replace('"', "").replace("`", "") if match else ""
