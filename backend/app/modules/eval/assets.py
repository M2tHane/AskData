import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.core.deps import CurrentUser, Role
from app.core.errors import AppError, ErrorCode
from app.core.masking import MaskKind, MaskRule
from app.core.permission import PermissionService, TableGrant
from app.modules.eval.schemas import EvalCaseInput


class EvalAssets:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        try:
            self.fixtures: dict[str, Any] = yaml.safe_load(
                (directory / "fixtures.yaml").read_text(encoding="utf-8")
            )
            self.cases = [
                EvalCaseInput.model_validate(json.loads(line))
                for line in (directory / "cases.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, ValueError, ValidationError, yaml.YAMLError):
            raise AppError(ErrorCode.VALIDATION_ERROR, "Invalid evaluation assets") from None
        self.team_ids = {team["id"]: i + 1 for i, team in enumerate(self.fixtures["teams"])}
        self.datasource_ids = {name: i + 1 for i, name in enumerate(self.fixtures["datasources"])}
        self.users = {
            item["id"]: CurrentUser(
                id=i + 1,
                role=Role(item["role"]),
                team_id=self.team_ids.get(item["team_id"]),
            )
            for i, item in enumerate(self.fixtures["users"])
        }
        self.permissions = PermissionService(self)
        if len({case.id for case in self.cases}) != len(self.cases):
            raise AppError(ErrorCode.VALIDATION_ERROR)

    async def snapshot(self, datasource_id: int, team_id: int | None) -> list[TableGrant]:
        datasource = next(
            (name for name, value in self.datasource_ids.items() if value == datasource_id), None
        )
        if datasource is None:
            raise AppError(ErrorCode.NOT_FOUND)
        team = next((name for name, value in self.team_ids.items() if value == team_id), None)
        grants = self.fixtures["grants"].get(team, {}).get(datasource, {})
        schema = self.fixtures["datasources"][datasource]["schema"]
        result = []
        for table, columns in self.fixtures["catalog"][datasource].items():
            grant = grants.get(table, {})
            masks = {
                name: MaskRule(kind=MaskKind.PHONE if value == "phone" else MaskKind.FULL)
                for name, value in grant.get("masks", {}).items()
            }
            result.append(
                TableGrant(
                    name=f"{schema}.{table}",
                    all_columns=dict.fromkeys(columns, "TEXT"),
                    team_columns=frozenset(grant.get("leader_columns", [])),
                    member_columns=frozenset(grant.get("member_columns", [])),
                    team_visible=bool(grant.get("leader_columns")),
                    member_visible=bool(grant.get("member_columns")),
                    masks=masks,
                )
            )
        return result

    async def hidden_names(self, case: EvalCaseInput) -> set[str]:
        user = self.users[case.as_user]
        ds_id = self.datasource_ids[case.datasource]
        scope = await self.permissions.visible_scope(user, ds_id)
        hidden = set()
        for grant in await self.snapshot(ds_id, user.team_id):
            table = scope.tables.get(grant.name)
            if table is None:
                hidden.add(grant.name.rsplit(".", 1)[-1])
            else:
                hidden.update(set(grant.all_columns) - set(table.columns))
        return hidden
