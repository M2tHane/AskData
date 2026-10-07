import json
from enum import StrEnum
from pathlib import Path
from string import Template

from pydantic import BaseModel

from app.core.errors import AppError, ErrorCode


class PromptId(StrEnum):
    SQL = "sql_generation"
    CLARIFY = "clarification"
    CORRECT = "correction"
    CHART = "chart"
    EXPLAIN = "explanation"


class RenderedPrompt(BaseModel):
    template: PromptId
    version: str
    content: str


class PromptRegistry:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(__file__).resolve().parents[1] / "prompts"
        self.versions = dict.fromkeys(PromptId, "v1")

    def render(
        self,
        prompt: PromptId,
        data: dict[str, object],
        version: str | None = None,
        dialect_hint: str = "",
    ) -> RenderedPrompt:
        version = version or self.versions[prompt]
        if version != self.versions[prompt] or prompt not in self.versions:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        template = Template((self.directory / f"{prompt.value}.{version}.txt").read_text())
        # JSON escapes prevent input containing delimiter tokens from ending the data block.
        serialized = (
            json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e")
        )
        return RenderedPrompt(
            template=prompt,
            version=version,
            content=template.substitute(
                data=serialized,
                dialect=dialect_hint,
            ),
        )
