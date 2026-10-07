from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, Role, require_role
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope
from app.modules.eval.schemas import EvalCaseInput, Evaluation, RunComparison, RunRequest, RunView
from app.modules.eval.service import EvalService

router = APIRouter(prefix="/eval", tags=["eval"])
Writer = Annotated[CurrentUser, Depends(require_role(Role.ADMIN))]
Reader = Annotated[CurrentUser, Depends(require_role(Role.ADMIN, Role.LEADER))]


def eval_service() -> EvalService:
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)


Service = Annotated[EvalService, Depends(eval_service)]


@router.post("/cases", response_model=Envelope[EvalCaseInput])
async def save_case(
    case: EvalCaseInput, service: Service, actor: Writer
) -> Envelope[EvalCaseInput]:
    return Envelope(data=await service.save_case(case, actor=actor))


@router.post("/cases/load", response_model=Envelope[int])
async def load_cases(service: Service, actor: Writer) -> Envelope[int]:
    return Envelope(data=await service.load_cases(actor=actor))


@router.post("/runs", response_model=Envelope[RunView])
async def start(request: RunRequest, service: Service, actor: Writer) -> Envelope[RunView]:
    return Envelope(data=await service.start(request, actor=actor))


@router.get("/runs/compare", response_model=Envelope[RunComparison])
async def compare(
    before: int, after: int, service: Service, actor: Reader
) -> Envelope[RunComparison]:
    return Envelope(data=await service.compare(before, after, actor=actor))


@router.get("/runs/{identifier}", response_model=Envelope[RunView])
async def get(identifier: int, service: Service, actor: Reader) -> Envelope[RunView]:
    return Envelope(data=await service.get(identifier, actor=actor))


@router.get("/runs/{identifier}/results", response_model=Envelope[list[Evaluation]])
async def results(identifier: int, service: Service, actor: Reader) -> Envelope[list[Evaluation]]:
    return Envelope(data=await service.results(identifier, actor=actor))
