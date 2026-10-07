"""Debug mode must never supply an anonymous admin identity."""

from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from dynamic_agents.auth.auth import get_user_context
from dynamic_agents.config import Settings, get_settings
from dynamic_agents.models import UserContext


def test_debug_mode_does_not_create_an_admin_session() -> None:
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings.model_construct(debug=True)

    @app.get("/context")
    async def context(user: Annotated[UserContext, Depends(get_user_context)]) -> dict[str, str]:
        return {"email": user.email}

    with TestClient(app) as client:
        response = client.get("/context")
    assert response.status_code == 401
