from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import CurrentUser, current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope, Page, PageParams
from app.modules.feedback.schemas import (
    ConversionInput,
    ExampleInput,
    ExampleReview,
    ExampleView,
    FeedbackInput,
    FeedbackView,
    MyFeedback,
)
from app.modules.feedback.service import FeedbackService

router = APIRouter(prefix="/feedback", tags=["feedback"], dependencies=[Depends(current_user)])


def get_service(request: Request) -> FeedbackService:
    service = getattr(request.state, "feedback_service", None)
    if not isinstance(service, FeedbackService):
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
    return service


Service = Annotated[FeedbackService, Depends(get_service)]
User = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[Page[FeedbackView]])
async def list_items(
    page: Annotated[PageParams, Depends()],
    service: Service,
    user: User,
    status: str | None = None,
    user_id: Annotated[int | None, Query(ge=1)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Envelope[Page[FeedbackView]]:
    return Envelope(
        data=await service.list(user, page, status, user_id=user_id, since=since, until=until)
    )


@router.get("/mine", response_model=Envelope[list[MyFeedback]])
async def mine(
    service: Service,
    user: User,
    query_run_ids: Annotated[list[int], Query(max_length=200)],
) -> Envelope[list[MyFeedback]]:
    return Envelope(data=await service.mine(query_run_ids, actor=user))


@router.post("", response_model=Envelope[FeedbackView])
async def submit(data: FeedbackInput, service: Service, user: User) -> Envelope[FeedbackView]:
    return Envelope(data=await service.submit(data, actor=user))


@router.post("/{identifier}/handle", response_model=Envelope[FeedbackView])
async def handle(identifier: int, service: Service, user: User) -> Envelope[FeedbackView]:
    return Envelope(data=await service.handle(identifier, actor=user))


@router.post("/{identifier}/convert", response_model=Envelope[FeedbackView])
async def convert(
    identifier: int, data: ConversionInput, service: Service, user: User
) -> Envelope[FeedbackView]:
    return Envelope(data=await service.convert(identifier, data, actor=user))


@router.get("/examples/list", response_model=Envelope[list[ExampleView]])
async def examples(
    datasource_id: int, service: Service, user: User, approved_only: bool = True
) -> Envelope[list[ExampleView]]:
    return Envelope(data=await service.examples(user, datasource_id, approved_only=approved_only))


@router.post("/examples", response_model=Envelope[ExampleView])
async def create_example(data: ExampleInput, service: Service, user: User) -> Envelope[ExampleView]:
    return Envelope(data=await service.create_example(data, actor=user))


@router.post("/examples/{identifier}/review", response_model=Envelope[ExampleView])
async def review_example(
    identifier: int, data: ExampleReview, service: Service, user: User
) -> Envelope[ExampleView]:
    return Envelope(data=await service.review_example(identifier, data, actor=user))
