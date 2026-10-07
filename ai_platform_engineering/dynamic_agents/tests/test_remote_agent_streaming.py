"""Streaming accumulation and transport-neutral encoder contracts."""

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from a2a.types import (
    Artifact,
    Part,
    StreamResponse,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from dynamic_agents.services.remote_agent_tool import _RemoteOutput, create_remote_agent_tool
from dynamic_agents.services.stream_encoders.agui_sse import AGUIStreamEncoder
from dynamic_agents.services.stream_encoders.custom_sse import CustomStreamEncoder


def test_artifact_replacement_and_append_do_not_duplicate_text() -> None:
    output = _RemoteOutput()
    for text, append in [("initial", False), ("replacement", False), (" tail", True)]:
        output.update(
            StreamResponse(
                artifact_update=TaskArtifactUpdateEvent(
                    task_id="test-task",
                    context_id="test-context",
                    artifact=Artifact(artifact_id="answer", parts=[Part(text=text)]),
                    append=append,
                )
            )
        )
    assert output.text == "replacement tail"
    assert output.stream_seen and not output.finished
    output.update(
        StreamResponse(
            status_update=TaskStatusUpdateEvent(
                task_id="test-task",
                context_id="test-context",
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )
    )
    assert output.finished
    assert output.text == "replacement tail"


@pytest.mark.parametrize(
    "state", [TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_CANCELED, TaskState.TASK_STATE_REJECTED]
)
def test_stream_failure_is_not_a_successful_partial_answer(state: TaskState) -> None:
    with pytest.raises(RuntimeError, match="failed or was canceled"):
        _RemoteOutput().update(
            StreamResponse(
                status_update=TaskStatusUpdateEvent(
                    task_id="test-task",
                    context_id="test-context",
                    status=TaskStatus(state=state),
                )
            )
        )


@pytest.mark.parametrize("encoder", [AGUIStreamEncoder, CustomStreamEncoder])
def test_encoder_preserves_tool_output_snapshot_and_namespace(encoder: type) -> None:
    frames = encoder()._handle_custom(
        {"type": "tool_output", "tool_call_id": "call-1", "result": "partial"}, ("test-child",)
    )
    assert len(frames) == 1
    data = json.loads(next(line[6:] for line in frames[0].splitlines() if line.startswith("data: ")))
    if encoder is AGUIStreamEncoder:
        assert data["name"] == "TOOL_OUTPUT"
        data = data["value"]
    assert data["tool_call_id"] == "call-1"
    assert data["result"] == "partial"
    assert data["namespace"] == ["test-child"]


@pytest.mark.parametrize("ending", ["eof", "timeout", "cancel"])
async def test_stream_cleanup_on_disconnect_timeout_and_cancellation(
    ending: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    async def stream(*args: Any, **kwargs: Any) -> AsyncIterator[StreamResponse]:
        yield StreamResponse(
            artifact_update=TaskArtifactUpdateEvent(
                task_id="task",
                context_id="context",
                artifact=Artifact(artifact_id="answer", parts=[Part(text="partial")]),
            )
        )
        started.set()
        if ending == "timeout":
            raise httpx.ReadTimeout("test timeout")
        if ending == "cancel":
            await asyncio.Event().wait()

    client = SimpleNamespace(send_message=stream, close=AsyncMock())
    factory = SimpleNamespace(create_from_url=AsyncMock(return_value=client))
    monkeypatch.setattr("dynamic_agents.services.remote_agent_tool.ClientFactory", lambda config: factory)
    tool = await create_remote_agent_tool(a2a_url="http://agent.example.test", streaming=True, bearer_token="caller")
    if ending == "cancel":
        task = asyncio.create_task(tool.ainvoke({"message": "test"}))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        error = RuntimeError if ending == "eof" else httpx.ReadTimeout
        with pytest.raises(error):
            await tool.ainvoke({"message": "test"})
    client.close.assert_awaited_once()
