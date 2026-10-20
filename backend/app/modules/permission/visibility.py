"""Pure resolution of hierarchical team visibility: datasource -> table -> column.

An uncustomized object takes the team set of its parent. A customized object keeps the
teams it was given, clipped to its parent's effective set, so removing a team above always
revokes it below.
"""

from dataclasses import dataclass, field


@dataclass
class ColumnNode:
    id: int
    custom: bool = False
    teams: set[int] = field(default_factory=set)


@dataclass
class TableNode:
    id: int
    custom: bool = False
    teams: set[int] = field(default_factory=set)
    columns: list[ColumnNode] = field(default_factory=list)


@dataclass(frozen=True)
class Resolved:
    tables: dict[int, set[int]]
    columns: dict[int, set[int]]


def resolve(datasource_teams: set[int], tables: list[TableNode]) -> Resolved:
    table_sets: dict[int, set[int]] = {}
    column_sets: dict[int, set[int]] = {}
    for table in tables:
        effective = (table.teams & datasource_teams) if table.custom else set(datasource_teams)
        table_sets[table.id] = effective
        for column in table.columns:
            column_sets[column.id] = (column.teams & effective) if column.custom else set(effective)
    return Resolved(table_sets, column_sets)


def derive_overrides(datasource_teams: set[int], tables: list[TableNode]) -> None:
    """Mark objects whose materialized teams differ from what they would inherit."""
    for table in tables:
        table.custom = table.teams != datasource_teams
        for column in table.columns:
            column.custom = column.teams != table.teams
