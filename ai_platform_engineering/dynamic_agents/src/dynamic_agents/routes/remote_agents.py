"""Remote A2A agent registry probe using the official SDK card resolver."""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from a2a.client import A2ACardResolver, AgentCardResolutionError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dynamic_agents.auth.auth import UserContext, require_admin
from dynamic_agents.auth.token_context import current_user_token
from dynamic_agents.config import get_settings
from dynamic_agents.models import RemoteAgentCredentialSource
from dynamic_agents.services.a2a_destination import A2ADestinationPolicy
from dynamic_agents.services.remote_agent_tool import resolve_remote_agent_auth_headers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/remote-agents", tags=["remote-agents"])


class RemoteAgentProbeRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2048)
    credential_source: RemoteAgentCredentialSource | None = None


class RemoteAgentProbeResponse(BaseModel):
    name: str
    description: str
    protocol_version: str | None = None
    protocol_bindings: list[str]
    supports_streaming: bool = False


@router.post("/probe", response_model=RemoteAgentProbeResponse)
async def probe_remote_agent(
    payload: RemoteAgentProbeRequest,
    _user: Annotated[UserContext, Depends(require_admin)],
) -> RemoteAgentProbeResponse:
    """Resolve an Agent Card through the official A2A SDK."""
    settings = get_settings()
    try:
        policy = A2ADestinationPolicy(payload.endpoint, settings.remote_a2a_allowed_http_origins)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = current_user_token.get()
    try:
        headers = await resolve_remote_agent_auth_headers(
            payload.credential_source.model_dump() if payload.credential_source else None,
            caller_token=token,
            credential_api_url=settings.credential_api_url,
            credential_service_audience=settings.credential_service_audience,
        )
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0), follow_redirects=False, event_hooks={"request": [policy.request_hook(headers)]}
        ) as client:
            card = await A2ACardResolver(client, payload.endpoint).get_agent_card()
    except (AgentCardResolutionError, httpx.HTTPError, ValueError, RuntimeError) as exc:
        logger.info("A2A card probe failed for %s: %s", payload.endpoint, exc)
        raise HTTPException(status_code=502, detail="Could not resolve A2A Agent Card with the configured destination and authentication") from exc

    interfaces = list(getattr(card, "supported_interfaces", []) or [])
    return RemoteAgentProbeResponse(
        supports_streaming=card.capabilities.streaming,
        name=card.name,
        description=card.description,
        protocol_version=interfaces[0].protocol_version if interfaces else None,
        protocol_bindings=list(dict.fromkeys(interface.protocol_binding for interface in interfaces)),
    )
