from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse


class EventType(StrEnum):
    STAGE = "stage"
    SQL = "sql"
    DATA = "data"
    CHART = "chart"
    EXPLANATION = "explanation"
    # Incremental model thinking while the SQL is being written: {"delta": str}.
    REASONING = "reasoning"
    # Incremental data-grounded answer written after execution: {"delta": str}.
    ANSWER = "answer"
    # The conversation got a generated title: {"title": str}.
    TITLE = "title"
    ERROR = "error"
    DONE = "done"


class StreamEvent(BaseModel):
    event: EventType
    data: dict[str, Any] = Field(default_factory=dict)
    id: int = Field(ge=0)

    def encode(self) -> str:
        from json import dumps

        payload = dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        return f"id: {self.id}\nevent: {self.event.value}\ndata: {payload}\n\n"


def sse_response(events: AsyncIterator[StreamEvent]) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        async for event in events:
            yield event.encode()

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
