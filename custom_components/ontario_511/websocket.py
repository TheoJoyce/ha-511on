"""WebSocket command the map card uses to know where areas and commutes are."""

from __future__ import annotations

from typing import Any

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
import probatio

from .commute import generation_lines, linked_areas
from .const import (
    CONF_CORRIDOR_KM,
    CONF_LOCATION,
    CONF_RADIUS_KM,
    DOMAIN,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
)


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register the integration's WebSocket commands."""
    websocket_api.async_register_command(hass, ws_map_scopes)


def _circle(area: ConfigSubentry) -> dict[str, Any]:
    location = area.data[CONF_LOCATION]
    return {
        "center": [location["latitude"], location["longitude"]],
        "radius_km": area.data[CONF_RADIUS_KM],
    }


def _scope(
    registry: er.EntityRegistry, entry: ConfigEntry, subentry: ConfigSubentry
) -> dict[str, Any] | None:
    base = {"id": subentry.subentry_id, "title": subentry.title}
    if subentry.subentry_type == SUBENTRY_AREA:
        return {**base, "type": "area", **_circle(subentry)}
    if subentry.subentry_type != SUBENTRY_COMMUTE:
        return None
    return {
        **base,
        "type": "commute",
        "route_entity": registry.async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{subentry.subentry_id}:commute_route"
        ),
        "corridor_km": subentry.data[CONF_CORRIDOR_KM],
        "lines": [
            [[round(lat, 5), round(lon, 5)] for lat, lon in line]
            for line in generation_lines(subentry.data)
        ],
        "areas": [_circle(area) for area in linked_areas(entry, subentry)],
    }


@websocket_api.websocket_command({probatio.Required("type"): "ontario_511/map_scopes"})
@callback
def ws_map_scopes(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return every loaded area and commute, for scoping the map card."""
    registry = er.async_get(hass)
    scopes = [
        scope
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
        for subentry in entry.subentries.values()
        if (scope := _scope(registry, entry, subentry)) is not None
    ]
    connection.send_result(msg["id"], {"scopes": scopes})
