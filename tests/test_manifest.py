"""Sanity checks for the integration manifest."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from custom_components.ontario_511.const import DOMAIN


async def test_integration_loads(hass: HomeAssistant) -> None:
    """The custom integration is discoverable under its domain."""
    integration = await async_get_integration(hass, DOMAIN)
    assert integration.domain == DOMAIN
