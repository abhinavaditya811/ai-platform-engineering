"""Debug mode cannot create a user or promote a gateway user to admin."""

import base64
import hashlib
import hmac
import json
import time
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from dynamic_agents.auth.auth import get_current_user, get_user_context, require_admin
from dynamic_agents.config import Settings, get_settings
from dynamic_agents.models import UserContext


@pytest.fixture(params=[False, True], ids=["normal", "debug"])
def client(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DA_USER_CONTEXT_HMAC_SECRET", "test-signing-key")
    debug = request.param
    monkeypatch.setenv("DEBUG", "true" if debug else "false")
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings.model_construct(debug=debug)

    # Exercise the dependency directly so JWT middleware cannot mask a regression.
    @app.get("/context")
    async def context(user: Annotated[UserContext, Depends(get_user_context)]) -> dict[str, Any]:
        return user.model_dump()

    @app.get("/current")
    async def current(user: Annotated[UserContext, Depends(get_current_user)]) -> dict[str, Any]:
        return user.model_dump()

    @app.get("/admin")
    async def admin(user: Annotated[UserContext, Depends(require_admin)]) -> dict[str, str]:
        return {"email": user.email}

    return TestClient(app)


def _header(**fields: Any) -> dict[str, str]:
    payload = {"email": "test-user@example.test", "name": "Test User", **fields}
    return _signed(base64.b64encode(json.dumps(payload).encode()).decode())


def _signed(context: str, bearer: str = "Bearer test-token", timestamp: int | None = None) -> dict[str, str]:
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    digest = hmac.new(b"test-signing-key", f"{stamp}\n{bearer}\n{context}".encode(), hashlib.sha256).hexdigest()
    return {"X-User-Context": context, "Authorization": bearer,
            "X-User-Context-Timestamp": stamp, "X-User-Context-Signature": f"v2={digest}"}


@pytest.mark.parametrize("headers", [{}, {"X-User-Context": ""}])
def test_missing_context_requires_authentication(client: TestClient, headers: dict[str, str]) -> None:
    response = client.get("/context", headers=headers)
    assert response.status_code == 401
    assert "Missing X-User-Context" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    ["not-base64", base64.b64encode(b"not-json").decode(), base64.b64encode(b"{}").decode()],
)
def test_malformed_context_is_rejected(client: TestClient, payload: str) -> None:
    response = client.get("/context", headers=_signed(payload))
    assert response.status_code == 400
    assert response.json()["detail"] == "Malformed X-User-Context header"


@pytest.mark.parametrize("path", ["/context", "/current"])
def test_gateway_user_is_preserved_without_elevation(client: TestClient, path: str) -> None:
    response = client.get(
        path, headers=_header(sub="test-subject", is_admin=False, can_view_admin=False, groups=["test-group"])
    )
    assert response.status_code == 200
    context = response.json()
    assert context["email"] == "test-user@example.test"
    assert context["name"] == "Test User"
    assert context["sub"] == "test-subject"
    assert context["is_admin"] is False
    assert context["can_view_admin"] is False
    assert context["groups"] == ["test-group"]


def test_non_admin_cannot_access_admin_endpoint(client: TestClient) -> None:
    response = client.get("/admin", headers=_header(is_admin=False))
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin role required"


def test_gateway_admin_can_access_admin_endpoint(client: TestClient) -> None:
    response = client.get("/admin", headers=_header(is_admin=True))
    assert response.status_code == 200
    assert response.json() == {"email": "test-user@example.test"}


def test_missing_context_cannot_access_admin_endpoint(client: TestClient) -> None:
    response = client.get("/admin")
    assert response.status_code == 401


@pytest.mark.parametrize("mutation", ["context", "bearer", "timestamp", "signature", "unsigned"])
def test_forged_gateway_admin_is_rejected(client: TestClient, mutation: str) -> None:
    headers = _header(is_admin=False)
    if mutation == "context":
        headers["X-User-Context"] = _header(is_admin=True)["X-User-Context"]
    elif mutation == "bearer":
        headers["Authorization"] = "Bearer another-valid-token"
    elif mutation == "timestamp":
        headers["X-User-Context-Timestamp"] = "invalid"
    elif mutation == "signature":
        headers["X-User-Context-Signature"] = "v2=invalid"
    else:
        headers.pop("X-User-Context-Signature")
    assert client.get("/admin", headers=headers).status_code == 401


def test_expired_signed_context_is_rejected(client: TestClient) -> None:
    context = _header(is_admin=True)["X-User-Context"]
    assert client.get("/admin", headers=_signed(context, timestamp=int(time.time())-121)).status_code == 401


def test_missing_signing_configuration_fails_closed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DA_USER_CONTEXT_HMAC_SECRET")
    assert client.get("/admin", headers=_header(is_admin=True)).status_code == 503
