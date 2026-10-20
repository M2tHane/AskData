from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, current_user
from app.core.errors import AppError, ErrorCode
from app.core.response import Envelope
from app.modules.mcpaccess.schemas import (
    McpClientRead,
    McpClientStatus,
    McpClientWrite,
    McpKeyCreated,
    McpKeyRead,
    McpKeyRotate,
    OverlayItem,
    OverrideRestore,
    OverrideWrite,
    TestAskReply,
    TestAskRequest,
)
from app.modules.mcpaccess.service import AgentService, McpAccessService
from app.modules.permission.service import ClientGrantsRead, ClientGrantsWrite

router = APIRouter(prefix="/mcp-clients", tags=["mcp"], dependencies=[Depends(current_user)])


def get_service() -> McpAccessService:
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)


def agent_service() -> AgentService:
    raise AppError(ErrorCode.INTERNAL_ERROR, http_status=503)


Service = Annotated[McpAccessService, Depends(get_service)]
Agent = Annotated[AgentService, Depends(agent_service)]
User = Annotated[CurrentUser, Depends(current_user)]


@router.get("", response_model=Envelope[list[McpClientRead]])
async def list_clients(service: Service, user: User) -> Envelope[list[McpClientRead]]:
    return Envelope(data=await service.clients(actor=user))


@router.post("", response_model=Envelope[McpKeyCreated], status_code=201)
async def create_client(
    data: McpClientWrite, service: Service, user: User
) -> Envelope[McpKeyCreated]:
    """The response carries the connection's first key; it is never shown again."""
    client, key = await service.create(data, actor=user)
    return Envelope(data=key.model_copy(update={"client_id": client.id}))


@router.get("/{client_id}", response_model=Envelope[McpClientRead])
async def get_client(client_id: int, service: Service, user: User) -> Envelope[McpClientRead]:
    return Envelope(data=await service.get(client_id, actor=user))


@router.put("/{client_id}", response_model=Envelope[McpClientRead])
async def update_client(
    client_id: int, data: McpClientWrite, service: Service, user: User
) -> Envelope[McpClientRead]:
    return Envelope(data=await service.update(client_id, data, actor=user))


@router.post("/{client_id}/status", response_model=Envelope[McpClientRead])
async def set_status(
    client_id: int, data: McpClientStatus, service: Service, user: User
) -> Envelope[McpClientRead]:
    return Envelope(data=await service.set_status(client_id, data.active, actor=user))


@router.delete("/{client_id}", response_model=Envelope[dict[str, bool]])
async def delete_client(client_id: int, service: Service, user: User) -> Envelope[dict[str, bool]]:
    await service.delete(client_id, actor=user)
    return Envelope(data={"deleted": True})


@router.post("/{client_id}/keys/rotate", response_model=Envelope[McpKeyCreated])
async def rotate_key(
    client_id: int, data: McpKeyRotate, service: Service, user: User
) -> Envelope[McpKeyCreated]:
    key = await service.rotate_key(client_id, data.grace_hours, actor=user)
    return Envelope(data=key.model_copy(update={"client_id": client_id}))


@router.post("/{client_id}/keys/{key_id}/revoke", response_model=Envelope[McpKeyRead])
async def revoke_key(
    client_id: int, key_id: int, service: Service, user: User
) -> Envelope[McpKeyRead]:
    return Envelope(data=await service.revoke_key(client_id, key_id, actor=user))


@router.get("/{client_id}/grants/{datasource_id}", response_model=Envelope[ClientGrantsRead])
async def read_grants(
    client_id: int, datasource_id: int, service: Service, user: User
) -> Envelope[ClientGrantsRead]:
    return Envelope(data=await service.read_grants(client_id, datasource_id, actor=user))


@router.put("/{client_id}/grants/{datasource_id}", response_model=Envelope[ClientGrantsRead])
async def replace_grants(
    client_id: int, datasource_id: int, data: ClientGrantsWrite, service: Service, user: User
) -> Envelope[ClientGrantsRead]:
    return Envelope(data=await service.replace_grants(client_id, datasource_id, data, actor=user))


@router.get("/{client_id}/items", response_model=Envelope[list[OverlayItem]])
async def list_items(
    client_id: int, datasource_id: int, service: Service, user: User
) -> Envelope[list[OverlayItem]]:
    return Envelope(data=await service.overlay_items(client_id, datasource_id, actor=user))


@router.put("/{client_id}/items", response_model=Envelope[OverlayItem])
async def set_item(
    client_id: int, datasource_id: int, data: OverrideWrite, service: Service, user: User
) -> Envelope[OverlayItem]:
    return Envelope(data=await service.set_override(client_id, datasource_id, data, actor=user))


@router.post("/{client_id}/items/restore", response_model=Envelope[dict[str, int]])
async def restore_items(
    client_id: int, data: OverrideRestore, service: Service, user: User
) -> Envelope[dict[str, int]]:
    return Envelope(data={"restored": await service.restore(client_id, data, actor=user)})


@router.post("/{client_id}/test", response_model=Envelope[TestAskReply])
async def test_ask(
    client_id: int, data: TestAskRequest, agent_service: Agent, user: User
) -> Envelope[TestAskReply]:
    return Envelope(data=await agent_service.test(client_id, data, actor=user))
