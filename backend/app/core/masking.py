import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MaskKind(StrEnum):
    PHONE = "phone"
    ID_CARD = "id_card"
    CUSTOM = "custom"
    FULL = "full"


class MaskRule(BaseModel):
    kind: MaskKind
    prefix: int = Field(default=0, ge=0, le=8)
    suffix: int = Field(default=0, ge=0, le=8)

    @model_validator(mode="after")
    def safe_custom(self) -> "MaskRule":
        if self.kind == MaskKind.CUSTOM and self.prefix + self.suffix > 8:
            raise ValueError("Custom masks may expose at most eight characters")
        return self


def mask_value(value: Any, rule: MaskRule) -> Any:
    if value is None:
        return None
    text = str(value)
    prefix, suffix = rule.prefix, rule.suffix
    if rule.kind == MaskKind.PHONE:
        prefix, suffix = 3, 4
    elif rule.kind == MaskKind.ID_CARD:
        prefix, suffix = 3, 2
    elif rule.kind == MaskKind.FULL:
        return "********"
    if len(text) <= prefix + suffix:
        return "********"
    return text[:prefix] + "*" * (len(text) - prefix - suffix) + (text[-suffix:] if suffix else "")


def mask_rows(rows: list[dict[str, Any]], rules: dict[str, MaskRule]) -> list[dict[str, Any]]:
    return [
        {
            key: mask_value(value, rules[key]) if key in rules else value
            for key, value in row.items()
        }
        for row in rows
    ]


def safe_export_cell(value: Any) -> Any:
    if isinstance(value, str) and re.match(r"^[\s]*[=+@-]", value):
        return "'" + value
    return value
