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
