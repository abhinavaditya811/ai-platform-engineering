"""Live weather A2A agent using Open-Meteo, served behind the JWT gateway."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from a2a.types import AgentSkill
from fastapi import FastAPI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from dynamic_agents.services.example_a2a_server import create_example_app
from dynamic_agents.services.llm_clients import get_llm

logger = logging.getLogger(__name__)
AGENT_URL = os.getenv("WEATHER_AGENT_URL", "http://weather-agentgateway:4000/")


async def fetch_weather(city: str, client: httpx.AsyncClient) -> dict[str, Any]:
    """Resolve a city and fetch current weather plus a three-day forecast."""
    if len(city.strip()) < 2 or len(city) > 200:
        raise ValueError("Provide a city name between 2 and 200 characters.")
    response = await client.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city.strip(), "count": 1, "language": "en", "format": "json"},
    )
    response.raise_for_status()
    locations = response.json().get("results", [])
    if not locations:
        raise ValueError("City not found. Include the country or region to clarify the location.")
    location = locations[0]
    response = await client.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 3,
            "timezone": "auto",
        },
    )
    response.raise_for_status()
    forecast = response.json()
    if not forecast.get("current") or not forecast.get("daily"):
        raise ValueError("Weather provider returned an incomplete forecast.")
    return {
        "location": ", ".join(str(location[k]) for k in ("name", "admin1", "country") if location.get(k)),
        "timezone": forecast.get("timezone"),
        "current": forecast["current"],
        "current_units": forecast.get("current_units", {}),
        "daily": forecast["daily"],
        "daily_units": forecast.get("daily_units", {}),
        "attribution": "Weather data: Open-Meteo (https://open-meteo.com/), CC BY 4.0; geocoding: GeoNames.",
    }


@tool
async def get_weather(city: str) -> str:
    """Get live weather and a three-day forecast for a city; include its region or country if ambiguous."""
    try:
        # A separate client prevents caller credentials from reaching the public weather provider.
        async with httpx.AsyncClient(timeout=20) as client:
            return json.dumps(await fetch_weather(city, client), ensure_ascii=False)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Weather lookup failed (%s)", type(exc).__name__)
        if isinstance(exc, ValueError):
            return str(exc)
        return "Weather data is currently unavailable. Please try again later."


async def _build_agent() -> Any:
    return create_react_agent(
        get_llm(os.getenv("LLM_PROVIDER", ""), ""),
        [get_weather],
        prompt="You provide live weather. Always use get_weather for weather questions. Ask for a city if missing. "
        "Report the resolved location, observation time, units, and Open-Meteo attribution. "
        "Never invent weather when the provider fails.",
    )


def create_app(agent: Any | None = None, agent_url: str | None = None) -> FastAPI:
    return create_example_app(
        name="Weather Agent",
        description="Live current weather and three-day forecasts by city, from Open-Meteo.",
        skill=AgentSkill(
            id="weather",
            name="Weather forecast",
            description="Look up current conditions and forecasts for a city.",
            tags=["weather", "forecast"],
            examples=["What is the current weather in a city?"],
        ),
        # All discovered calls stay on the public gateway, never the private backend.
        agent_url=agent_url or AGENT_URL,
        build_agent=_build_agent,
        agent=agent,
    )


app = create_app()
