"""Offline weather provider, gateway configuration, and official SDK contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from dynamic_agents.weather_agent.server import create_app, fetch_weather, get_weather


async def test_weather_provider_and_units_without_caller_credentials() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "authorization" not in request.headers
        if request.url.host == "geocoding-api.open-meteo.com":
            assert request.url.params["name"] == "Example City"
            return httpx.Response(200, json={"results": [{"name": "Example City", "latitude": 10, "longitude": 20}]})
        assert request.url.host == "api.open-meteo.com"
        assert request.url.params["forecast_days"] == "3"
        return httpx.Response(
            200,
            json={
                "timezone": "UTC",
                "current": {"temperature_2m": 21},
                "current_units": {"temperature_2m": "°C"},
                "daily": {"temperature_2m_max": [22, 23, 24]},
                "daily_units": {"temperature_2m_max": "°C"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await fetch_weather(" Example City ", client)
    assert len(requests) == 2
    assert result["location"] == "Example City"
    assert result["current"]["temperature_2m"] == 21
    assert result["current_units"]["temperature_2m"] == "°C"
    assert "Open-Meteo" in result["attribution"]


@pytest.mark.parametrize("city", ["", "a", "x" * 201])
async def test_invalid_city_rejected_without_network(city: str) -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="city name"):
            await fetch_weather(city, client)


async def test_unknown_city_is_actionable() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))) as client:
        with pytest.raises(ValueError, match="City not found"):
            await fetch_weather("Example City", client)


async def test_provider_failure_returns_no_invented_weather(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(city: str, client: httpx.AsyncClient) -> dict:
        raise httpx.ConnectError("provider unavailable")

    monkeypatch.setattr("dynamic_agents.weather_agent.server.fetch_weather", fail)
    assert "unavailable" in await get_weather.ainvoke({"city": "Example City"})


class _WeatherAgent:
    async def ainvoke(self, input: dict) -> dict:
        return {"messages": [SimpleNamespace(content="Example City: 21 °C")]}


async def test_weather_card_advertises_gateway_and_sdk_rpc_works() -> None:
    gateway_url = "http://gateway.example.test:4000/"
    app = create_app(agent=_WeatherAgent(), agent_url=gateway_url)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=gateway_url) as client:
            card = (await client.get("/.well-known/agent-card.json")).json()
            assert card["supportedInterfaces"][0]["url"] == gateway_url
            response = await client.post(
                "/",
                headers={"A2A-Version": "1.0"},
                json={
                    "jsonrpc": "2.0",
                    "id": "test",
                    "method": "SendMessage",
                    "params": {
                        "message": {"messageId": "test-message", "role": "ROLE_USER", "parts": [{"text": "weather"}]}
                    },
                },
            )
            assert response.status_code == 200
            assert "error" not in response.json()
            assert "21 °C" in response.text


def test_weather_gateway_requires_user_jwt_and_private_backend() -> None:
    root = Path(__file__).resolve().parents[3]
    config = yaml.safe_load((root / "deploy/agentgateway/config.a2a-weather.yaml").read_text())
    listener = config["binds"][0]["listeners"][0]
    jwt = listener["policies"]["jwtAuth"]
    assert jwt["mode"] == "strict"
    assert "caipe-platform" in jwt["audiences"]
    assert set(jwt["jwtValidationOptions"]["requiredClaims"]) == {"exp", "sub", "iss", "aud"}
    assert listener["routes"][0]["backends"] == [{"host": "weather-agent:8121"}]
    assert "a2a" not in listener["routes"][0].get("policies", {})
    compose = yaml.safe_load((root / "docker-compose.dev.yaml").read_text())
    weather = compose["services"]["weather-agent"]
    assert not weather.get("ports")
    assert weather["networks"] == ["weather_a2a"]
    assert compose["services"]["weather-agentgateway"]["networks"] == ["default", "weather_a2a"]
