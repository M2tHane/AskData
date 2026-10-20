from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.response import Envelope
from app.modules.auth.schemas import LoginRequest, RefreshRequest, TokenPair
from app.modules.auth.service import AuthService, get_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
Service = Annotated[AuthService, Depends(get_service)]
Actor = Annotated[CurrentUser, Depends(current_user)]


@router.post("/login", response_model=Envelope[TokenPair])
async def login(data: LoginRequest, service: Service) -> Envelope[TokenPair]:
    return Envelope(data=await service.login(data))


@router.post("/refresh", response_model=Envelope[TokenPair])
async def refresh(data: RefreshRequest, service: Service) -> Envelope[TokenPair]:
    return Envelope(data=await service.rotate(data.refresh_token))


@router.post("/logout", response_model=Envelope[None])
async def logout(data: RefreshRequest, service: Service, actor: Actor) -> Envelope[None]:
    await service.logout(data.refresh_token, actor=actor)
    return Envelope()


@router.get("/me", response_model=Envelope[CurrentUser])
async def me(actor: Actor) -> Envelope[CurrentUser]:
    return Envelope(data=actor)
