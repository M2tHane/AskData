import json
from enum import StrEnum
from functools import cache
from pathlib import Path
from string import Template
from typing import Protocol

import yaml
from pydantic import BaseModel

from app.core.errors import AppError, ErrorCode


class PromptId(StrEnum):
    # One call writes the SQL and also decides clarification, chart, title and interpretation.
    SQL = "sql_generation"
    CORRECT = "correction"
    # One call writes the data-grounded answer after execution.
    ANSWER = "answer"
    SUGGEST = "suggestion"


DIALECT_NAMES = ("mysql", "postgresql", "kingbase", "gaussdb", "clickhouse")
DATA_BLOCK = "<untrusted_data>$data</untrusted_data>"
MAX_PROMPT_CHARS = 20_000


class ActiveRevision(BaseModel):
    """An administrator-published replacement for a seed prompt; label shows up in query logs."""

    label: str
    content: str


class PromptOverrides(BaseModel):
    system: dict[str, ActiveRevision] = {}
    dialects: dict[str, ActiveRevision] = {}


class PromptOverrideSource(Protocol):
    async def load(self) -> PromptOverrides: ...


def validate_template(variables: list[str], content: str) -> None:
    """A published prompt keeps the registered variables and the injection-safe data block."""
    text = content.strip()
    if not text or len(text) > MAX_PROMPT_CHARS or DATA_BLOCK not in text:
        raise AppError(ErrorCode.VALIDATION_ERROR)
    try:
        template = Template(text)
        identifiers = sorted(template.get_identifiers())
        template.substitute(dict.fromkeys(variables, ""))
    except (ValueError, KeyError):
        raise AppError(ErrorCode.VALIDATION_ERROR) from None
    if identifiers != sorted(variables):
        raise AppError(ErrorCode.VALIDATION_ERROR)


def validate_dialect_text(content: str) -> None:
    if not content.strip() or len(content) > MAX_PROMPT_CHARS:
        raise AppError(ErrorCode.VALIDATION_ERROR)


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
        self.versions = {
            PromptId.SQL: "v8",
            PromptId.CORRECT: "v3",
            PromptId.ANSWER: "v3",
            PromptId.SUGGEST: "v1",
        }
        self._templates: dict[PromptId, tuple[_LoadedPrompt, Template]] = {
            prompt: self._load(prompt, self.directory / f"{prompt.value}.{version}.yaml")
            for prompt, version in self.versions.items()
        }

    def seed(self, prompt: PromptId) -> tuple[str, list[str], str]:
        """Built-in version label, registered variables and template text."""
        loaded, _ = self._templates[prompt]
        return loaded.version, list(loaded.variables), loaded.template

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
        overrides: PromptOverrides | None = None,
    ) -> RenderedPrompt:
        version = version or self.versions[prompt]
        if version != self.versions[prompt] or prompt not in self.versions:
            raise AppError(ErrorCode.NOT_FOUND, http_status=404)
        loaded, template = self._templates[prompt]
        published = overrides.system.get(prompt.value) if overrides else None
        if published is not None:
            loaded = loaded.model_copy(update={"version": published.label})
            template = Template(published.content)
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
