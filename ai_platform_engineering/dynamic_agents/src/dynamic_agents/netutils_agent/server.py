"""Expose the netutils MCP tools through an official SDK A2A server."""

from __future__ import annotations

import os
from typing import Any

from a2a.types import AgentSkill
from fastapi import FastAPI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

from dynamic_agents.services.example_a2a_server import create_example_app
from dynamic_agents.services.llm_clients import get_llm

NETUTILS_MCP_URL = os.getenv("NETUTILS_MCP_URL", "http://mcp-netutils:8000/mcp")
AGENT_URL = os.getenv("NETUTILS_AGENT_URL", "http://netutils-agent:8120/")


async def _build_agent() -> Any:
    mcp_client = MultiServerMCPClient(
        {"netutils": {"transport": "streamable_http", "url": NETUTILS_MCP_URL}},
        tool_name_prefix=True,
    )
    tools = await mcp_client.get_tools()
    if not tools:
        raise RuntimeError(f"No netutils MCP tools discovered at {NETUTILS_MCP_URL}")
    return create_react_agent(get_llm(os.getenv("LLM_PROVIDER", ""), ""), tools)


def create_app(agent: Any | None = None, agent_url: str | None = None) -> FastAPI:
    return create_example_app(
        name="Netutils Agent",
        description="Network diagnostics, DNS lookup, and network utility tools.",
        skill=AgentSkill(
            id="netutils",
            name="Network utilities",
            description="Run network diagnostics and utility operations.",
            tags=["network", "dns", "diagnostics"],
            examples=["Resolve example.com", "Check whether port 443 is reachable on example.com"],
        ),
        agent_url=agent_url or AGENT_URL,
        build_agent=_build_agent,
        agent=agent,
    )


app = create_app()
