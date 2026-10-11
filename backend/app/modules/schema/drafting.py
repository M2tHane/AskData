"""Model-drafted column semantics for datasource setup. Only names, types and the meanings
already known go to the model, never data."""

from pydantic import BaseModel, ValidationError

from app.core.deps import CurrentUser
from app.core.errors import AppError, ErrorCode
from app.core.llm import CallContext, ChatMessage, LLMClient, extract_json_object
from app.core.prompt import PromptId, PromptOverrideSource, PromptRegistry
from app.modules.schema.schemas import ColumnRead, TableRead

# Columns per model call: a wide table is drafted in a few calls, not one huge prompt.
BATCH = 60


class _Output(BaseModel):
    columns: dict[str, str]


class ModelDescriptionDrafter:
    def __init__(
        self,
        llm: LLMClient,
        prompts: PromptRegistry | None = None,
        overrides: PromptOverrideSource | None = None,
    ) -> None:
        self.llm, self.prompts, self.overrides = llm, prompts or PromptRegistry(), overrides

    async def draft(
        self, table: TableRead, columns: list[ColumnRead], *, actor: CurrentUser
    ) -> dict[int, str]:
        known = [
            {
                "name": column.name,
                "type": column.data_type,
                "meaning": column.description or column.comment,
            }
            for column in table.columns
        ]
        overrides = await self.overrides.load() if self.overrides else None
        found: dict[int, str] = {}
        for start in range(0, len(columns), BATCH):
            batch = {column.name: column.id for column in columns[start : start + BATCH]}
            rendered = self.prompts.render(
                PromptId.DESCRIBE,
                {
                    "table": table.qualified_name,
                    "comment": table.comment,
                    "description": table.description,
                    "columns": known,
                    "describe": list(batch),
                },
                overrides=overrides,
            )
            try:
                response = await self.llm.chat(
                    [ChatMessage(role="system", content=rendered.content)],
                    CallContext(
                        user_id=actor.id,
                        team_id=actor.team_id,
                        prompt_version=f"{PromptId.DESCRIBE.value}:{rendered.version}",
                        reasoning_effort="low",
                    ),
                )
                output = _Output.model_validate_json(extract_json_object(response.text))
            except (ValidationError, ValueError):
                raise AppError(ErrorCode.LLM_UNAVAILABLE, http_status=503) from None
            found.update(
                {batch[name]: text for name, text in output.columns.items() if name in batch}
            )
        return found
