"""Repairs: planning old commutes again, along roads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from . import Ontario511ConfigEntry
from .api import Ontario511Error, Resource
from .commute import async_replan
from .coordinator import async_fetch_resource
from .directions import RoutingFailed, RoutingUnavailable
from .models import RoadSegment


class ReplanCommuteFlow(RepairsFlow):
    """Plan a commute's routes again, once the user confirms."""

    def __init__(self, entry_id: str, subentry_id: str) -> None:
        """Initialize the flow for one commute."""
        self._entry_id = entry_id
        self._subentry_id = subentry_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Start with the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Explain what happens, then plan the routes."""
        entry: Ontario511ConfigEntry | None = self.hass.config_entries.async_get_entry(
            self._entry_id
        )
        if (
            entry is None
            or (commute := entry.subentries.get(self._subentry_id)) is None
        ):
            return self.async_abort(reason="commute_removed")
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            runtime = entry.runtime_data
            try:
                data = await async_fetch_resource(
                    self.hass, runtime.client, runtime.key, Resource.ROAD_CONDITIONS
                )
            except Ontario511Error:
                errors["base"] = "cannot_connect"
            else:
                segments = [s for s in data.values() if isinstance(s, RoadSegment)]
                try:
                    new_data = await async_replan(self.hass, entry, commute, segments)
                except RoutingUnavailable:
                    errors["base"] = "routing_unavailable"
                except RoutingFailed:
                    errors["base"] = "routing_failed"
                else:
                    self.hass.config_entries.async_update_subentry(
                        entry, commute, data=new_data
                    )
                    return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            errors=errors,
            description_placeholders={"commute": commute.title},
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the flow that fixes an issue."""
    if TYPE_CHECKING:
        assert data is not None
    return ReplanCommuteFlow(str(data["entry_id"]), str(data["subentry_id"]))
