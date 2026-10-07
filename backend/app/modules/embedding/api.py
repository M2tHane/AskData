from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope
from app.core.tasks import TaskManager, TaskRecord
from app.modules.embedding.schemas import IndexDocument, IndexTaskInput, SearchInput
from app.modules.embedding.service import EmbeddingService

router = APIRouter(prefix="/embedding", tags=["embedding"], dependencies=[Depends(current_user)])


def get_service(request: Request) -> EmbeddingService:
    service = getattr(request.state, "embedding_service", None)
    if not isinstance(service, EmbeddingService):
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
    return service


def get_manager(request: Request) -> TaskManager:
    manager = getattr(request.app.state, "task_manager", None)
    if not isinstance(manager, TaskManager):
        raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)
    return manager


Service = Annotated[EmbeddingService, Depends(get_service)]
User = Annotated[CurrentUser, Depends(current_user)]
Manager = Annotated[TaskManager, Depends(get_manager)]


@router.post("/update", response_model=Envelope[TaskRecord])
async def update(
    data: IndexTaskInput, service: Service, manager: Manager, user: User
) -> Envelope[TaskRecord]:
    return Envelope(data=await service.enqueue(data.datasource_id, manager, actor=user))


@router.post("/rebuild", response_model=Envelope[TaskRecord])
async def rebuild(
    data: IndexTaskInput, service: Service, manager: Manager, user: User
) -> Envelope[TaskRecord]:
    return Envelope(
        data=await service.enqueue(data.datasource_id, manager, actor=user, rebuild=True)
    )


@router.post("/search", response_model=Envelope[list[IndexDocument]])
async def search(data: SearchInput, service: Service, user: User) -> Envelope[list[IndexDocument]]:
    return Envelope(data=await service.search(data, user))
