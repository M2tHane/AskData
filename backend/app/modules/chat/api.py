from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse

from app.core.deps import CurrentUser, current_user
from app.core.response import Envelope, Page, PageParams
from app.core.stream import sse_response
from app.modules.chat.schemas import (
    ConversationCreate,
    ConversationView,
    MessageView,
    QueryReply,
    QuestionRequest,
    RerunRequest,
)
from app.modules.chat.service import ChatService, get_service

router = APIRouter(prefix="/api/chat", tags=["chat"])

chat_service = get_service


User = Annotated[CurrentUser, Depends(current_user)]
Service = Annotated[ChatService, Depends(chat_service)]
Pagination = Annotated[PageParams, Depends()]


@router.post("/conversations", response_model=Envelope[ConversationView], status_code=201)
async def create(
    request: ConversationCreate, user: User, service: Service
) -> Envelope[ConversationView]:
    return Envelope(data=await service.create(request, actor=user))


@router.get("/conversations", response_model=Envelope[Page[ConversationView]])
async def list_conversations(
    user: User, service: Service, page: Pagination
) -> Envelope[Page[ConversationView]]:
    return Envelope(data=await service.list(page, actor=user))


@router.get("/conversations/{identifier}", response_model=Envelope[ConversationView])
async def get_conversation(
    identifier: int, user: User, service: Service
) -> Envelope[ConversationView]:
    return Envelope(data=await service.get(identifier, actor=user))


@router.delete("/conversations/{identifier}", response_model=Envelope[dict[str, bool]])
async def delete(identifier: int, user: User, service: Service) -> Envelope[dict[str, bool]]:
    await service.delete(identifier, actor=user)
    return Envelope(data={"deleted": True})


@router.get("/conversations/{identifier}/messages", response_model=Envelope[Page[MessageView]])
async def messages(
    identifier: int, user: User, service: Service, page: Pagination
) -> Envelope[Page[MessageView]]:
    return Envelope(data=await service.messages(identifier, page, actor=user))


@router.post("/conversations/{identifier}/messages", response_model=Envelope[QueryReply])
async def ask(
    identifier: int, request: QuestionRequest, user: User, service: Service
) -> Envelope[QueryReply]:
    return Envelope(data=await service.ask(identifier, request, actor=user))


@router.post("/conversations/{identifier}/messages/stream", response_class=StreamingResponse)
async def stream(
    identifier: int, request: QuestionRequest, user: User, service: Service
) -> StreamingResponse:
    ctx = await service.prepare(identifier, request, actor=user)
    return sse_response(service.stream(ctx))


@router.post("/conversations/{identifier}/rerun", response_model=Envelope[QueryReply])
async def rerun(
    identifier: int, request: RerunRequest, user: User, service: Service
) -> Envelope[QueryReply]:
    return Envelope(data=await service.rerun(identifier, request, actor=user))


@router.get("/conversations/{identifier}/messages/{message_id}/export")
async def export(
    identifier: int,
    message_id: int,
    user: User,
    service: Service,
    format: Annotated[Literal["csv", "xlsx"], Query()] = "csv",
) -> Response:
    result = await service.export(identifier, message_id, format, actor=user)
    return Response(
        result.content,
        media_type=result.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
        },
    )
