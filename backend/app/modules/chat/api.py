from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse

from app.core.deps import CurrentUser, current_user
from app.core.response import Envelope, Page, PageParams
from app.core.stream import sse_response
from app.modules.chat.schemas import (
    ChatLimits,
    ContextPreview,
    ContextPreviewRequest,
    ConversationCreate,
    ConversationRename,
    ConversationView,
    MessageView,
    QueryReply,
    QuestionRequest,
    RerunRequest,
    SuggestedQuestions,
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
    user: User,
    service: Service,
    page: Pagination,
    q: Annotated[str, Query(max_length=200)] = "",
) -> Envelope[Page[ConversationView]]:
    return Envelope(data=await service.list(page, actor=user, query=q))


@router.get("/conversations/{identifier}", response_model=Envelope[ConversationView])
async def get_conversation(
    identifier: int, user: User, service: Service
) -> Envelope[ConversationView]:
    return Envelope(data=await service.get(identifier, actor=user))


@router.patch("/conversations/{identifier}", response_model=Envelope[ConversationView])
async def rename(
    identifier: int, request: ConversationRename, user: User, service: Service
) -> Envelope[ConversationView]:
    return Envelope(data=await service.rename(identifier, request, actor=user))


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


@router.post("/context-preview", response_model=Envelope[ContextPreview])
async def context_preview(
    request: ContextPreviewRequest, user: User, service: Service
) -> Envelope[ContextPreview]:
    return Envelope(data=await service.context_preview(request, actor=user))


@router.get("/suggested-questions", response_model=Envelope[SuggestedQuestions])
async def suggested_questions(
    datasource_id: Annotated[int, Query(ge=1)], user: User, service: Service
) -> Envelope[SuggestedQuestions]:
    return Envelope(data=await service.suggested_questions(datasource_id, actor=user))


@router.get("/limits", response_model=Envelope[ChatLimits])
async def limits(user: User, service: Service) -> Envelope[ChatLimits]:
    return Envelope(data=service.limits())


@router.post("/conversations/{identifier}/rerun", response_model=Envelope[QueryReply])
async def rerun(
    identifier: int, request: RerunRequest, user: User, service: Service
) -> Envelope[QueryReply]:
    return Envelope(data=await service.rerun(identifier, request, actor=user))


@router.get(
    "/conversations/{identifier}/messages/{message_id}/replay",
    response_model=Envelope[QueryReply],
)
async def replay(
    identifier: int, message_id: int, user: User, service: Service
) -> Envelope[QueryReply]:
    return Envelope(data=await service.replay(identifier, message_id, actor=user))


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
            "X-Export-Rows": str(result.row_count),
            "X-Export-Truncated": "1" if result.truncated else "0",
            "Access-Control-Expose-Headers": "X-Export-Rows, X-Export-Truncated",
        },
    )
