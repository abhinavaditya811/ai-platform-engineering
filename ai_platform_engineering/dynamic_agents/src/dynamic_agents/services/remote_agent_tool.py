"""A2A SDK-backed remote-agent tools for Dynamic Agents."""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from a2a.client import ClientCallContext, ClientCallInterceptor, ClientConfig, ClientFactory
from a2a.client.interceptors import AfterArgs, BeforeArgs
from a2a.types import Message, Part, Role, SendMessageRequest
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from dynamic_agents.auth.token_context import current_user_token
from dynamic_agents.models import RemoteAgentCredentialSource
from dynamic_agents.services.credential_exchange import CredentialExchangeClient

_UNSAFE_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")


def _sanitize_tool_name(raw: str) -> str:
    """Reduce a remote-agent name to a tool name accepted by model APIs."""
    return _UNSAFE_NAME_CHARS.sub("_", raw).strip("_").lower()


def _field_is_set(value: Any, field: str) -> bool:
    """Check a protobuf field while tolerating SDK response wrappers."""
    has_field = getattr(value, "HasField", None)
    if callable(has_field):
        try:
            return has_field(field)
        except ValueError:
            return False
    return getattr(value, field, None) is not None


def _text_parts(message: Any) -> list[str]:
    parts = getattr(message, "parts", None) or []
    return [part.text for part in parts if getattr(part, "text", None)]


def _response_text(response: Any) -> list[str]:
    """Extract visible output from SDK direct-message and task responses."""
    texts: list[str] = []
    if _field_is_set(response, "message"):
        texts.extend(_text_parts(response.message))
    if _field_is_set(response, "task"):
        task = response.task
        for artifact in getattr(task, "artifacts", None) or []:
            texts.extend(_text_parts(artifact))
        status = getattr(task, "status", None)
        if status is not None and _field_is_set(status, "message"):
            texts.extend(_text_parts(status.message))
    if _field_is_set(response, "artifact_update"):
        texts.extend(_text_parts(response.artifact_update.artifact))
    if _field_is_set(response, "status_update"):
        status = response.status_update.status
        if _field_is_set(status, "message"):
            texts.extend(_text_parts(status.message))
    return texts


class _RemoteAgentInput(BaseModel):
    message: str = Field(description="The message to send to the remote agent")


class _RequestHeadersInterceptor(ClientCallInterceptor):
    """Attach the selected authentication headers to every SDK request."""

    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = headers

    async def before(self, args: BeforeArgs) -> None:
        if not self._headers:
            return
        context = args.context or ClientCallContext()
        headers = dict(context.service_parameters or {})
        headers.update(self._headers)
        context.service_parameters = headers
        args.context = context

    async def after(self, args: AfterArgs) -> None:
        return None


async def resolve_remote_agent_auth_headers(
    credential_source: dict[str, Any] | None,
    *,
    caller_token: str | None,
    credential_api_url: str | None,
    credential_service_audience: str = "caipe-credential-service",
) -> dict[str, str]:
    """Resolve one caller, saved-secret, or connected-account header."""
    source = RemoteAgentCredentialSource.model_validate(credential_source or {})
    kind = source.kind
    header_name = source.name
    credential: str | None = None

    if kind == "caller_token":
        credential = caller_token
    elif kind in {"secret_ref", "provider_connection"}:
        if not caller_token:
            raise RuntimeError("Caller authentication is required to resolve the A2A credential")
        if not credential_api_url:
            raise RuntimeError("Credential service is not configured for this A2A agent")
        credential_client = CredentialExchangeClient(
            base_url=credential_api_url,
            audience=credential_service_audience,
            token_provider=lambda: caller_token,
        )
        if kind == "secret_ref":
            credential = await credential_client.retrieve_secret(source.secret_ref, intended_use="a2a_agent")
        else:
            exchanged = await credential_client.exchange_provider_connection_by_provider(
                source.provider,
                intended_use="a2a_agent",
            )
            access_token = exchanged.get("access_token")
            credential = access_token if isinstance(access_token, str) else None

    if not credential or not credential.strip():
        raise RuntimeError(f"A2A agent authentication credential is unavailable for {header_name}")
    header_value = credential.strip()
    if header_name.lower() == "authorization" and not header_value.lower().startswith("bearer "):
        header_value = f"Bearer {header_value}"
    return {header_name: header_value}


class RemoteAgentTool(BaseTool):
    """LangChain tool that delegates through the official A2A Python SDK."""

    name: str
    description: str
    a2a_url: str
    bearer_token: str | None = None
    credential_source: dict[str, Any] | None = None
    credential_api_url: str | None = None
    credential_service_audience: str = "caipe-credential-service"
    timeout: int = 120

    args_schema: type[BaseModel] = _RemoteAgentInput

    def _run(self, message: str) -> str:
        raise NotImplementedError("RemoteAgentTool is async-only; use ainvoke()")

    async def _resolve_auth_headers(self, caller_token: str | None) -> dict[str, str]:
        return await resolve_remote_agent_auth_headers(
            self.credential_source,
            caller_token=caller_token,
            credential_api_url=self.credential_api_url,
            credential_service_audience=self.credential_service_audience,
        )

    async def _arun(self, message: str) -> str:
        # The runtime cache outlives individual requests, so resolve caller-scoped
        # auth at tool-call time using the active request token.
        token = current_user_token.get() or self.bearer_token
        headers = await self._resolve_auth_headers(token)
        timeout = httpx.Timeout(float(self.timeout))
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as http_client:
            factory = ClientFactory(
                ClientConfig(
                    streaming=False,
                    supported_protocol_bindings=["JSONRPC", "HTTP+JSON"],
                    httpx_client=http_client,
                )
            )
            client = await factory.create_from_url(
                self.a2a_url,
                interceptors=[_RequestHeadersInterceptor(headers)],
                resolver_http_kwargs={"timeout": timeout},
            )
            try:
                request = SendMessageRequest(
                    message=Message(
                        message_id=str(uuid.uuid4()),
                        role=Role.ROLE_USER,
                        parts=[Part(text=message)],
                    )
                )
                results: list[str] = []
                context = ClientCallContext(timeout=float(self.timeout))
                async for response in client.send_message(request, context=context):
                    results.extend(_response_text(response))
                return "\n".join(results) or "Remote agent returned no text response."
            finally:
                await client.close()


async def create_remote_agent_tool(
    *,
    a2a_url: str,
    name: str | None = None,
    description: str | None = None,
    bearer_token: str | None = None,
    credential_source: dict[str, Any] | None = None,
    credential_api_url: str | None = None,
    credential_service_audience: str = "caipe-credential-service",
    timeout: int = 120,
) -> RemoteAgentTool:
    """Create a tool from registry metadata; SDK handles protocol negotiation.

    Args:
        a2a_url: Base URL of the remote A2A agent.
        name: Registry or Agent Card name override.
        description: Registry or Agent Card description.
        bearer_token: Fallback token used only when no request token is bound.
        credential_source: Header authentication source configured for this endpoint.
        credential_api_url: Credential service URL used for secrets and OAuth connections.
        credential_service_audience: Credential service audience.
        timeout: Per-request timeout in seconds, configured in the UI registry.
    """
    safe_name = _sanitize_tool_name(name or "") or _sanitize_tool_name(urlparse(a2a_url).hostname or "")
    return RemoteAgentTool(
        name=safe_name or "remote_agent",
        description=description or f"Remote A2A agent at {a2a_url}",
        a2a_url=a2a_url,
        bearer_token=bearer_token,
        credential_source=credential_source,
        credential_api_url=credential_api_url,
        credential_service_audience=credential_service_audience,
        timeout=timeout,
    )
