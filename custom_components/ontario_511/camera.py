"""Traffic cameras: one camera entity per view.

Images come from Ontario 511's web map, not the rate-limited API. Each view's
image is cached (see policy.CAMERA_IMAGE_CACHE), so a dashboard showing it
fetches at most once a minute.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import Ontario511ConfigEntry, subentry_items
from .api import REQUEST_TIMEOUT, Resource
from .coordinator import ResourceCoordinator
from .entity import LOCATION_ATTRIBUTES, ItemEntity, location_attributes
from .models import Camera as CameraModel, CameraView
from .policy import CAMERA_IMAGE_CACHE

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ontario511ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one camera entity per view of each camera item.

    The camera list may still be loading in the background, so views are
    added as soon as they're known.
    """
    coordinator = entry.runtime_data.coordinators.get(Resource.CAMERAS)
    if coordinator is None:
        return
    created: set[tuple[str, str, int]] = set()

    @callback
    def _add_views() -> None:
        data = coordinator.data or {}
        for subentry in entry.subentries.values():
            items = subentry_items(subentry).get(Resource.CAMERAS, {})
            new: list[TrafficCamera] = []
            for item_id, name in items.items():
                camera = data.get(item_id)
                count = len(camera.views) if isinstance(camera, CameraModel) else 0
                for index in range(max(1, count)):
                    key = (subentry.subentry_id, item_id, index)
                    if key not in created:
                        created.add(key)
                        new.append(
                            TrafficCamera(
                                coordinator, subentry.subentry_id, item_id, name, index
                            )
                        )
            if new:
                async_add_entities(new, config_subentry_id=subentry.subentry_id)

    _add_views()
    entry.async_on_unload(coordinator.async_add_listener(_add_views))


class TrafficCamera(ItemEntity, Camera):
    """One view of a traffic camera."""

    _unrecorded_attributes = LOCATION_ATTRIBUTES | {"description"}

    def __init__(
        self,
        coordinator: ResourceCoordinator,
        subentry_id: str,
        item_id: str,
        item_name: str,
        index: int,
    ) -> None:
        """Initialize the camera view."""
        ItemEntity.__init__(
            self, coordinator, subentry_id, item_id, item_name, f"view_{index + 1}"
        )
        Camera.__init__(self)
        self._index = index
        self._attr_translation_key = "view"
        self._attr_translation_placeholders = {"view": str(index + 1)}

    def _view(self) -> CameraView | None:
        camera = self.item
        if not isinstance(camera, CameraModel) or self._index >= len(camera.views):
            return None
        return camera.views[self._index]

    @property
    def available(self) -> bool:
        """Return whether this view exists and is enabled."""
        view = self._view()
        return super().available and view is not None and view.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return what the view shows; the first view carries the location.

        The camera's road and direction are sensors on the same device.
        """
        camera = self.item
        view = self._view()
        attrs: dict[str, Any] = {"description": view.description if view else None}
        if self._index == 0 and camera is not None:
            attrs |= location_attributes(camera.coordinates)
        return attrs

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the view's image, cached for a minute."""
        if (view := self._view()) is None:
            return None
        cache = self.coordinator.config_entry.runtime_data.camera_images
        now = dt_util.utcnow()
        cached = cache.get(view.url)
        if cached is not None and now - cached[0] < CAMERA_IMAGE_CACHE:
            return cached[1]
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(view.url, timeout=REQUEST_TIMEOUT) as response:
                response.raise_for_status()
                image = await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("Error fetching camera image %s: %s", view.id, err)
            return cached[1] if cached else None
        cache[view.url] = (now, image)
        return image
