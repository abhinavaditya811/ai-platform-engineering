"""Remote registry metadata and cache invalidation contracts."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dynamic_agents.services.agent_runtime import AgentRuntime
from dynamic_agents.services.mongo import MongoDBService


def test_registry_returns_auth_metadata_to_runtime() -> None:
    entry = {
        "_id": "remote-example", "name": "Example Agent", "endpoint": "https://agent.example.test",
        "credential_source": {"kind": "secret_ref", "name": "X-API-Key", "secret_ref": "example-secret"},
        "updated_at": "2026-01-01T00:00:00Z",
    }
    collection = Mock()
    collection.find.side_effect = lambda query, projection: [
        {key: value for key, value in entry.items() if projection.get(key)}
    ]
    service = object.__new__(MongoDBService)
    service._get_remote_agents_collection = lambda: collection

    loaded = service.get_remote_agents_by_ids(["remote-example"])
    assert loaded[0]["credential_source"] == entry["credential_source"]
    assert loaded[0]["updated_at"] == entry["updated_at"]


@pytest.mark.parametrize("latest", [
    [{"_id": "remote-example", "updated_at": "2026-01-02T00:00:00Z"}],
    [],  # Disabled or deleted endpoint.
])
def test_remote_auth_change_invalidates_cached_parent_or_subagent_runtime(latest: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    config = SimpleNamespace(updated_at=now, model="example")
    runtime = object.__new__(AgentRuntime)
    runtime.config = config
    runtime._config_updated_at = now
    runtime._mcp_servers_updated_at = datetime.min.replace(tzinfo=timezone.utc)
    runtime._remote_agent_versions = {"remote-example": "2026-01-01T00:00:00Z"}
    getter = Mock(return_value=[{"_id": "remote-example", "updated_at": "2026-01-01T00:00:00Z"}])
    runtime._mongo_service = SimpleNamespace(get_remote_agents_by_ids=getter)

    assert runtime.is_stale(config, []) is False
    getter.return_value = latest
    assert runtime.is_stale(config, []) is True
