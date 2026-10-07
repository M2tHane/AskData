import json
from enum import StrEnum
from functools import cache
from pathlib import Path
from string import Template

import yaml
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


class _LoadedPrompt(BaseModel):
    name: str
    version: str
    variables: list[str]
    template: str


def _load_prompt(path: Path) -> tuple[_LoadedPrompt, Template]:
    try:
        document = yaml.safe_load(path.read_text())
    except OSError as error:
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=500) from error
    if not isinstance(document, dict):
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=500)
    prompt = _LoadedPrompt.model_validate(document)
    placeholders = sorted(Template(prompt.template).get_identifiers())
    if prompt.name != path.name.split(".")[0] or placeholders != sorted(prompt.variables):
        # Unregistered or missing variables are configuration bugs, not runtime input.
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=500)
    return prompt, Template(prompt.template)


class PromptRegistry:
    """Loads every registered prompt once; business code never reads prompt files."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(__file__).resolve().parents[1] / "prompts"
        self.versions = dict.fromkeys(PromptId, "v2")
        self._templates: dict[PromptId, tuple[_LoadedPrompt, Template]] = {
            prompt: self._load(prompt, self.directory / f"{prompt.value}.{version}.yaml")
            for prompt, version in self.versions.items()
        }

    def _load(self, prompt: PromptId, path: Path) -> tuple[_LoadedPrompt, Template]:
        loaded, template = _load_prompt(path)
        if loaded.name != prompt.value:
            raise AppError(ErrorCode.INTERNAL_ERROR, http_status=500)
        return loaded, template

    def render(
        self,
        prompt: PromptId,
        data: dict[str, object],
        version: str | None = None,
        dialect_hint: str = "",
        engine: str = "",
    ) -> RenderedPrompt:
        version = version or self.versions[prompt]
        if version != self.versions[prompt] or prompt not in self.versions:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        loaded, template = self._templates[prompt]
        # JSON escapes prevent input containing delimiter tokens from ending the data block.
        serialized = (
            json.dumps(data, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e")
        )
        try:
            content = template.substitute(
                data=serialized,
                dialect=dialect_hint,
                engine=engine,
            )
        except KeyError:
            raise AppError(ErrorCode.INTERNAL_ERROR, http_status=500) from None
        return RenderedPrompt(template=prompt, version=loaded.version, content=content)


@cache
def _dialect_rules(directory: str, name: str) -> str:
    path = Path(directory) / f"{name}.v1.yaml"
    try:
        document = yaml.safe_load(path.read_text())
    except OSError as error:
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=500) from error
    if not isinstance(document, dict):
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=500)
    sections = document.get("template")
    if document.get("version") != "v1" or not isinstance(sections, dict) or not sections:
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=500)
    return "\n".join(str(value).strip() for value in sections.values())


def dialect_rules(dialect: str) -> str:
    """Render the registered dialect guidance; dialect files are core-registered assets."""
    directory = Path(__file__).resolve().parents[1] / "prompts" / "dialects"
    return _dialect_rules(str(directory), dialect)
