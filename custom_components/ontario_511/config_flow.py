"""Config flow for Ontario 511.

Initial setup asks for the API key, then a first area, then optionally a
commute. Areas, commutes and single items can be added later as subentries.
The data type checkboxes are only used once, to choose what to generate.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from types import MappingProxyType
from typing import Any

from homeassistant.components.zone import DATA_ZONE_STORAGE_COLLECTION
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import (
    CONF_API_KEY,
    CONF_ICON,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_RADIUS,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    AreaSelector,
    BooleanSelector,
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.loader import async_get_integration
import probatio

from . import Ontario511ConfigEntry
from .api import (
    Ontario511AuthError,
    Ontario511Client,
    Ontario511DevCacheError,
    Ontario511Error,
    Ontario511RateLimitError,
    Resource,
)
from .client import create_client, key_id
from .commute import (
    area_key,
    async_plan_commute,
    async_replan,
    change_segments,
    new_area_key,
    plan_highway_commute,
    route_lines,
)
from .const import (
    CONF_ADDRESS,
    CONF_CORRIDOR_KM,
    CONF_CREATE_ZONE,
    CONF_END,
    CONF_FEEDS,
    CONF_FROM_AREA,
    CONF_HA_AREA,
    CONF_ITEM_ID,
    CONF_ITEMS,
    CONF_LOCATION,
    CONF_PENDING,
    CONF_RADIUS_KM,
    CONF_REPLAN,
    CONF_RESOURCE,
    CONF_SEGMENTS,
    CONF_START,
    CONF_TO_AREA,
    CONF_TYPES,
    DEFAULT_AREA_NAME,
    DEFAULT_CORRIDOR_KM,
    DEFAULT_DESTINATION_NAME,
    DEFAULT_NEW_AREA_NAME,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    FAST_IMPORT_TYPES,
    FEED_RESOURCES,
    ITEM_RESOURCES,
    MAX_CORRIDOR_KM,
    MAX_RADIUS_KM,
    MIN_CORRIDOR_KM,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
    SUBENTRY_ITEM,
    VALIDATION_RESOURCE,
)
from .coordinator import async_fetch_resource
from .directions import RoutingFailed, RoutingUnavailable, async_get_router
from .geo import distance_km
from .geocode import (
    AddressNotFound,
    Geocoder,
    GeocodingFailed,
    GeocodingUnavailable,
    OutsideOntario,
    get_geocoder,
)
from .models import RoadSegment
from .scope import segments_within_corridor

_LOGGER = logging.getLogger(__name__)

TYPE_OPTIONS = [r.value for r in (*FEED_RESOURCES, *ITEM_RESOURCES)]
DEFAULT_TYPES = [
    r.value
    for r in (
        Resource.EVENTS,
        Resource.CONSTRUCTION,
        Resource.ALERTS,
        Resource.ROAD_CONDITIONS,
        Resource.CAMERAS,
        Resource.FERRY_TERMINALS,
        Resource.REST_AREAS,
        Resource.SERVICE_CENTRES,
    )
]
# Reconfigure offers segments this far beyond the corridor to add to a route.
NEARBY_SEGMENTS_KM = 10.0
MAX_ITEM_CHOICES = 200

KEY_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


def _types_selector(options: list[str]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=SelectSelectorMode.LIST,
            translation_key="data_types",
        )
    )


def _km_selector(minimum: float, maximum: float) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=0.1,
            unit_of_measurement="km",
            mode=NumberSelectorMode.BOX,
        )
    )


def _ha_area_field(current: str | None) -> probatio.Optional:
    """Return an optional Home Assistant area field, prefilled if known."""
    if current:
        return probatio.Optional(CONF_HA_AREA, description={"suggested_value": current})
    return probatio.Optional(CONF_HA_AREA)


def _with_ha_area(data: dict[str, Any], user_input: Mapping[str, Any]) -> None:
    """Store the chosen Home Assistant area, or none."""
    if ha_area := user_input.get(CONF_HA_AREA):
        data[CONF_HA_AREA] = ha_area
    else:
        data.pop(CONF_HA_AREA, None)


async def _async_create_zone(
    hass: HomeAssistant, name: str, location: Mapping[str, Any], radius_km: float
) -> None:
    """Create a Home Assistant zone matching an area.

    The zone belongs to the user from then on: it isn't changed or removed
    with the area.
    """
    await hass.data[DATA_ZONE_STORAGE_COLLECTION].async_create_item(
        {
            CONF_NAME: name,
            CONF_LATITUDE: location["latitude"],
            CONF_LONGITUDE: location["longitude"],
            CONF_RADIUS: radius_km * 1000,
            CONF_ICON: "mdi:map-marker-radius",
        }
    )


def _home(hass: HomeAssistant) -> dict[str, float]:
    return {"latitude": hass.config.latitude, "longitude": hass.config.longitude}


def _point(location: Mapping[str, Any]) -> tuple[float, float]:
    return (float(location["latitude"]), float(location["longitude"]))


def _split_types(types: list[str]) -> tuple[list[str], list[str]]:
    """Split selected types into feeds and item resources to generate."""
    feeds = [t for t in types if Resource(t) in FEED_RESOURCES]
    items = [t for t in types if Resource(t) in ITEM_RESOURCES]
    return feeds, items


async def _async_validate_key(hass: HomeAssistant, api_key: str) -> dict[str, str]:
    """Check a key with one rate-limited API call."""
    try:
        await create_client(hass, api_key).async_get(VALIDATION_RESOURCE)
    except Ontario511AuthError:
        return {"base": "invalid_auth"}
    except Ontario511RateLimitError:
        return {"base": "rate_limited"}
    except Ontario511DevCacheError:
        return {"base": "dev_cache_missing"}
    except Ontario511Error:
        return {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unexpected error validating the API key")
        return {"base": "unknown"}
    return {}


class _SetupSteps:
    """Area and commute steps shared by initial setup and subentry flows.

    An area is a map with a draggable pin and radius circle; Home Assistant's
    location selector shows the radius in metres. A commute runs from
    one area to another (``_from`` and ``_to``); its routes are worked out
    between their centres. Subclasses provide the API client and decide what
    happens once an area or commute is complete.
    """

    hass: HomeAssistant
    _draft: dict[str, Any]
    _type_count: int
    _area_name_default: str
    _from: dict[str, Any] | None
    _to: dict[str, Any] | None
    # Areas whose matching zone is created once the area is saved.
    _zones: list[tuple[str, dict[str, Any], float]]

    async_show_form: Any
    async_show_menu: Any

    def _client_and_key(self) -> tuple[Ontario511Client, str]:
        raise NotImplementedError

    async def _async_area_done(
        self, data: dict[str, Any], title: str, unique_id: str
    ) -> Any:
        raise NotImplementedError

    async def _async_commute_done(self, data: dict[str, Any], title: str) -> Any:
        raise NotImplementedError

    async def async_step_area(self, user_input: dict[str, Any] | None = None) -> Any:
        """Define an area on a map, and what to generate.

        Typing an address looks it up (OpenStreetMap Nominatim) and shows the
        form again with the pin moved there, so the result can be checked.
        """
        errors: dict[str, str] = {}
        found = ""
        defaults = user_input or {
            CONF_NAME: self._area_name_default,
            CONF_ADDRESS: "",
            CONF_LOCATION: {**_home(self.hass), "radius": DEFAULT_RADIUS_KM * 1000},
            CONF_TYPES: DEFAULT_TYPES,
            CONF_CREATE_ZONE: False,
        }
        if user_input is not None:
            address = (user_input.get(CONF_ADDRESS) or "").strip()
            # The location selector doesn't guarantee a radius.
            radius_m = float(
                user_input[CONF_LOCATION].get("radius", DEFAULT_RADIUS_KM * 1000)
            )
            radius_km = round(radius_m / 1000, 1)
            if address:
                try:
                    place = await (await self._async_geocoder()).async_lookup(address)
                except GeocodingUnavailable:
                    errors[CONF_ADDRESS] = "geocoding_unavailable"
                except GeocodingFailed:
                    errors[CONF_ADDRESS] = "geocoding_failed"
                except AddressNotFound:
                    errors[CONF_ADDRESS] = "address_not_found"
                except OutsideOntario:
                    errors[CONF_ADDRESS] = "outside_ontario"
                else:
                    defaults = {
                        **user_input,
                        CONF_ADDRESS: "",
                        CONF_LOCATION: {
                            "latitude": place.latitude,
                            "longitude": place.longitude,
                            "radius": radius_m,
                        },
                    }
                    found = f"📍 {place.name}"
            elif not user_input[CONF_TYPES]:
                errors[CONF_TYPES] = "no_types"
            elif radius_m > MAX_RADIUS_KM * 1000:
                errors[CONF_LOCATION] = "radius_too_large"
            elif user_input.get(CONF_CREATE_ZONE) and not radius_km:
                errors[CONF_CREATE_ZONE] = "zone_needs_radius"
            else:
                location = user_input[CONF_LOCATION]
                feeds, pending = _split_types(user_input[CONF_TYPES])
                self._type_count = len(user_input[CONF_TYPES])
                self._draft = {
                    CONF_NAME: user_input[CONF_NAME].strip() or self._area_name_default,
                    CONF_LOCATION: {
                        "latitude": location["latitude"],
                        "longitude": location["longitude"],
                    },
                    CONF_RADIUS_KM: radius_km,
                    CONF_FEEDS: feeds,
                    CONF_PENDING: pending,
                    CONF_ITEMS: {},
                }
                _with_ha_area(self._draft, user_input)
                self._create_zone = bool(user_input.get(CONF_CREATE_ZONE))
                self._draft_kind = SUBENTRY_AREA
                return await self._async_next_after_draft()
        schema = probatio.Schema(
            {
                probatio.Required(CONF_NAME, default=defaults[CONF_NAME]): str,
                probatio.Optional(
                    CONF_ADDRESS, default=defaults.get(CONF_ADDRESS, "")
                ): TextSelector(),
                probatio.Required(
                    CONF_LOCATION, default=defaults[CONF_LOCATION]
                ): LocationSelector(LocationSelectorConfig(radius=True)),
                probatio.Required(
                    CONF_TYPES, default=defaults[CONF_TYPES]
                ): _types_selector(TYPE_OPTIONS),
                _ha_area_field(defaults.get(CONF_HA_AREA)): AreaSelector(),
                probatio.Optional(
                    CONF_CREATE_ZONE, default=defaults.get(CONF_CREATE_ZONE, False)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="area",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "found": found,
                "max_radius": str(int(MAX_RADIUS_KM * 1000)),
            },
        )

    async def _async_geocoder(self) -> Geocoder:
        integration = await async_get_integration(self.hass, DOMAIN)
        return get_geocoder(self.hass, str(integration.version))

    async def async_step_commute(self, user_input: dict[str, Any] | None = None) -> Any:
        """Settings for a commute between two areas; routes are worked out."""
        assert self._from is not None and self._to is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_TYPES]:
                errors[CONF_TYPES] = "no_types"
            else:
                client, key = self._client_and_key()
                try:
                    segments = await async_fetch_resource(
                        self.hass, client, key, Resource.ROAD_CONDITIONS
                    )
                except Ontario511AuthError:
                    errors["base"] = "invalid_auth"
                except Ontario511RateLimitError:
                    errors["base"] = "rate_limited"
                except Ontario511Error:
                    errors["base"] = "cannot_connect"
                else:
                    self._commute_input = user_input
                    self._commute_segments = [
                        s for s in segments.values() if isinstance(s, RoadSegment)
                    ]
                    return await self._async_plan_commute(roads=True)
        defaults = user_input or {
            CONF_NAME: self._commute_name(),
            CONF_CORRIDOR_KM: DEFAULT_CORRIDOR_KM,
            CONF_TYPES: DEFAULT_TYPES,
            CONF_HA_AREA: self._from.get("ha_area"),
        }
        schema = probatio.Schema(
            {
                probatio.Required(CONF_NAME, default=defaults[CONF_NAME]): str,
                probatio.Required(
                    CONF_CORRIDOR_KM, default=defaults[CONF_CORRIDOR_KM]
                ): _km_selector(MIN_CORRIDOR_KM, MAX_CORRIDOR_KM),
                probatio.Required(
                    CONF_TYPES, default=defaults[CONF_TYPES]
                ): _types_selector(TYPE_OPTIONS),
                _ha_area_field(defaults.get(CONF_HA_AREA)): AreaSelector(),
            }
        )
        return self.async_show_form(
            step_id="commute",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "from": self._from["title"],
                "to": self._to["title"],
            },
        )

    async def _async_plan_commute(self, *, roads: bool) -> Any:
        """Plan the commute's routes, along roads or over highways only."""
        assert self._from is not None and self._to is not None
        start, end = self._from["centre"], self._to["centre"]
        if roads:
            try:
                plan = await async_plan_commute(
                    self.hass,
                    await async_get_router(self.hass),
                    self._commute_segments,
                    start,
                    end,
                )
            except RoutingUnavailable:
                return self._show_routing_menu("routing_unavailable")
            except RoutingFailed:
                return self._show_routing_menu("routing_failed")
        else:
            plan = await self.hass.async_add_executor_job(
                plan_highway_commute, self._commute_segments, start, end
            )
        user_input = self._commute_input
        feeds, pending = _split_types(user_input[CONF_TYPES])
        self._type_count = len(user_input[CONF_TYPES])
        self._draft_kind = SUBENTRY_COMMUTE
        self._draft = {
            CONF_NAME: user_input[CONF_NAME].strip() or self._commute_name(),
            CONF_FROM_AREA: self._from["key"],
            CONF_TO_AREA: self._to["key"],
            CONF_START: {"latitude": start[0], "longitude": start[1]},
            CONF_END: {"latitude": end[0], "longitude": end[1]},
            CONF_CORRIDOR_KM: round(float(user_input[CONF_CORRIDOR_KM]), 1),
            CONF_FEEDS: feeds,
            CONF_PENDING: pending,
            CONF_ITEMS: {},
            **plan,
        }
        _with_ha_area(self._draft, user_input)
        return await self._async_next_after_draft()

    def _show_routing_menu(self, reason: str) -> Any:
        return self.async_show_menu(
            step_id=reason,
            menu_options=["commute_retry", "commute_highways"],
        )

    async def async_step_routing_unavailable(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Offer highways only while road routing is switched off."""
        return self._show_routing_menu("routing_unavailable")

    async def async_step_routing_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Offer to try again, or highways only, after routing failed."""
        return self._show_routing_menu("routing_failed")

    async def async_step_commute_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Try routing along roads again."""
        return await self._async_plan_commute(roads=True)

    async def async_step_commute_highways(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Route over Ontario 511's highways only."""
        return await self._async_plan_commute(roads=False)

    def _commute_name(self) -> str:
        assert self._from is not None and self._to is not None
        return f"{self._from['title']} to {self._to['title']}"

    async def _async_next_after_draft(self) -> Any:
        if self._type_count > FAST_IMPORT_TYPES:
            return await self.async_step_slow_import()
        return await self._async_finish_draft()

    async def async_step_slow_import(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        """Warn that more than 10 data types take about a minute longer."""
        if user_input is not None:
            return await self._async_finish_draft()
        return self.async_show_form(
            step_id="slow_import",
            data_schema=probatio.Schema({}),
            description_placeholders={
                "fast": str(FAST_IMPORT_TYPES),
                "count": str(self._type_count),
            },
        )

    async def _async_finish_draft(self) -> Any:
        data = dict(self._draft)
        title = data.pop(CONF_NAME)
        if self._draft_kind == SUBENTRY_AREA:
            unique_id = new_area_key()
            area = {
                "key": unique_id,
                "title": title,
                "centre": _point(data[CONF_LOCATION]),
                "ha_area": data.get(CONF_HA_AREA),
            }
            if self._create_zone:
                self._zones.append((title, data[CONF_LOCATION], data[CONF_RADIUS_KM]))
            if self._from is None:
                self._from = area
            else:
                self._to = area
            return await self._async_area_done(data, title, unique_id)
        return await self._async_commute_done(data, title)

    async def _async_create_zones(self) -> None:
        for name, location, radius_km in self._zones:
            await _async_create_zone(self.hass, name, location, radius_km)
        self._zones = []

    _draft_kind: str
    _create_zone: bool = False
    _commute_input: dict[str, Any]
    _commute_segments: list[RoadSegment]


class Ontario511ConfigFlow(ConfigFlow, _SetupSteps, domain=DOMAIN):
    """Handle a config flow for Ontario 511."""

    VERSION = 2
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        self._api_key = ""
        self._subentries: list[ConfigSubentryData] = []
        self._draft = {}
        self._draft_kind = SUBENTRY_AREA
        self._type_count = 0
        self._area_name_default = DEFAULT_AREA_NAME
        self._from = None
        self._to = None
        self._zones = []

    def _client_and_key(self) -> tuple[Ontario511Client, str]:
        return create_client(self.hass, self._api_key), key_id(self._api_key)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            await self.async_set_unique_id(key_id(api_key))
            self._abort_if_unique_id_configured()
            if not (errors := await _async_validate_key(self.hass, api_key)):
                self._api_key = api_key
                return await self.async_step_area()  # type: ignore[no-any-return]
        return self.async_show_form(
            step_id="user", data_schema=KEY_SCHEMA, errors=errors
        )

    async def _async_area_done(
        self, data: dict[str, Any], title: str, unique_id: str
    ) -> ConfigFlowResult:
        self._subentries.append(
            ConfigSubentryData(
                data=data, subentry_type=SUBENTRY_AREA, title=title, unique_id=unique_id
            )
        )
        if self._to is None:
            return await self.async_step_add_commute()
        return await self.async_step_commute()  # type: ignore[no-any-return]

    async def async_step_add_commute(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to add a commute (to a second area) now, or finish."""
        return self.async_show_menu(
            step_id="add_commute", menu_options=["destination", "finish"]
        )

    async def async_step_destination(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add the area the commute goes to, such as Work."""
        self._area_name_default = DEFAULT_DESTINATION_NAME
        return await self.async_step_area()  # type: ignore[no-any-return]

    async def _async_commute_done(
        self, data: dict[str, Any], title: str
    ) -> ConfigFlowResult:
        self._subentries.append(
            ConfigSubentryData(
                data=data, subentry_type=SUBENTRY_COMMUTE, title=title, unique_id=None
            )
        )
        return await self.async_step_finish()

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry with its first area (and commute)."""
        await self._async_create_zones()
        return self.async_create_entry(
            title="Ontario 511",
            data={CONF_API_KEY: self._api_key},
            subentries=self._subentries,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the key was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            if not (errors := await _async_validate_key(self.hass, api_key)):
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    unique_id=key_id(api_key),
                    data_updates={CONF_API_KEY: api_key},
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=KEY_SCHEMA, errors=errors
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: Ontario511ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types: areas, commutes and single items."""
        return {
            SUBENTRY_AREA: AreaSubentryFlow,
            SUBENTRY_COMMUTE: CommuteSubentryFlow,
            SUBENTRY_ITEM: ItemSubentryFlow,
        }


class _EntrySubentryFlow(ConfigSubentryFlow):
    """Shared helpers for subentry flows."""

    def _client_and_key(self) -> tuple[Ontario511Client, str]:
        entry: Ontario511ConfigEntry = self._get_entry()
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data.client, entry.runtime_data.key
        api_key = entry.data[CONF_API_KEY]
        return create_client(self.hass, api_key), key_id(api_key)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Rename an area or commute, or change what it follows.

        Commutes can also change their corridor and the road segments they use.
        """
        subentry = self._get_reconfigure_subentry()
        is_commute = subentry.subentry_type == SUBENTRY_COMMUTE
        segments: dict[str, RoadSegment] = {}
        if is_commute:
            client, key = self._client_and_key()
            try:
                data = await async_fetch_resource(
                    self.hass, client, key, Resource.ROAD_CONDITIONS
                )
            except Ontario511Error:
                return self.async_abort(reason="cannot_connect")
            segments = {k: v for k, v in data.items() if isinstance(v, RoadSegment)}
        errors: dict[str, str] = {}
        if user_input is not None:
            new_data = {
                **subentry.data,
                CONF_FEEDS: user_input[CONF_FEEDS],
            }
            _with_ha_area(new_data, user_input)
            if is_commute:
                new_data[CONF_CORRIDOR_KM] = round(
                    float(user_input[CONF_CORRIDOR_KM]), 1
                )
                if user_input.get(CONF_REPLAN):
                    try:
                        new_data = await async_replan(
                            self.hass,
                            self._get_entry(),
                            subentry,
                            list(segments.values()),
                            new_data,
                        )
                    except RoutingUnavailable:
                        errors["base"] = "routing_unavailable"
                    except RoutingFailed:
                        errors["base"] = "routing_failed"
                else:
                    new_data.update(
                        change_segments(
                            subentry.data, user_input[CONF_SEGMENTS], segments
                        )
                    )
            if not errors:
                self._move_devices_if_changed(subentry, new_data)
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=user_input[CONF_NAME].strip() or subentry.title,
                    data=new_data,
                )
        fields: dict[Any, Any] = {
            probatio.Required(CONF_NAME, default=subentry.title): str,
            probatio.Required(
                CONF_FEEDS, default=list(subentry.data.get(CONF_FEEDS, []))
            ): _types_selector([r.value for r in FEED_RESOURCES]),
            _ha_area_field(subentry.data.get(CONF_HA_AREA)): AreaSelector(),
        }
        if is_commute:
            current = list(subentry.data.get(CONF_SEGMENTS, []))
            corridor = subentry.data[CONF_CORRIDOR_KM]
            nearby = segments_within_corridor(
                [s for i, s in segments.items() if i not in current],
                route_lines(subentry.data),
                corridor + NEARBY_SEGMENTS_KM,
            )
            options = [
                SelectOptionDict(value=i, label=segments[i].name)
                for i in current
                if i in segments
            ] + [SelectOptionDict(value=s.id, label=s.name) for s, _ in nearby]
            fields[probatio.Required(CONF_CORRIDOR_KM, default=corridor)] = (
                _km_selector(MIN_CORRIDOR_KM, MAX_CORRIDOR_KM)
            )
            fields[probatio.Optional(CONF_SEGMENTS, default=current)] = SelectSelector(
                SelectSelectorConfig(
                    options=options, multiple=True, mode=SelectSelectorMode.LIST
                )
            )
            fields[probatio.Optional(CONF_REPLAN, default=False)] = BooleanSelector()
        return self.async_show_form(
            step_id="reconfigure_commute" if is_commute else "reconfigure",
            data_schema=probatio.Schema(fields),
            errors=errors,
        )

    def _move_devices_if_changed(
        self, subentry: ConfigSubentry, new_data: Mapping[str, Any]
    ) -> None:
        """Move every device of a subentry to a newly chosen Home Assistant area."""
        area_id = new_data.get(CONF_HA_AREA)
        if not area_id or area_id == subentry.data.get(CONF_HA_AREA):
            return
        entry_id = self._get_entry().entry_id
        registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(registry, entry_id):
            if device.config_subentry_id == subentry.subentry_id:
                registry.async_update_device(device.id, area_id=area_id)

    async def async_step_reconfigure_commute(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the commute version of the reconfigure form."""
        return await self.async_step_reconfigure(user_input)


class AreaSubentryFlow(_EntrySubentryFlow, _SetupSteps):
    """Add an area."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._draft = {}
        self._draft_kind = SUBENTRY_AREA
        self._type_count = 0
        self._area_name_default = DEFAULT_NEW_AREA_NAME
        self._from = None
        self._to = None
        self._zones = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add an area."""
        return await self.async_step_area(user_input)  # type: ignore[no-any-return]

    async def _async_area_done(
        self, data: dict[str, Any], title: str, unique_id: str
    ) -> SubentryFlowResult:
        await self._async_create_zones()
        return self.async_create_entry(title=title, data=data, unique_id=unique_id)


class CommuteSubentryFlow(_EntrySubentryFlow, _SetupSteps):
    """Add a commute between two areas, optionally adding the second area."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._draft = {}
        self._draft_kind = SUBENTRY_COMMUTE
        self._type_count = 0
        self._area_name_default = DEFAULT_DESTINATION_NAME
        self._from = None
        self._to = None
        self._zones = []

    def _areas(self) -> dict[str, dict[str, Any]]:
        """Return the entry's areas by link key."""
        return {
            area_key(s): {
                "key": area_key(s),
                "title": s.title,
                "centre": _point(s.data[CONF_LOCATION]),
                "ha_area": s.data.get(CONF_HA_AREA),
            }
            for s in self._get_entry().subentries.values()
            if s.subentry_type == SUBENTRY_AREA
        }

    def _area_selector(self) -> SelectSelector:
        return SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=key, label=area["title"])
                    for key, area in self._areas().items()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose whether the commute goes to an existing area or a new one."""
        if not self._areas():
            return self.async_abort(reason="no_areas")
        return self.async_show_menu(
            step_id="user", menu_options=["existing_areas", "new_area"]
        )

    async def async_step_existing_areas(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the two areas the commute runs between."""
        errors: dict[str, str] = {}
        areas = self._areas()
        if user_input is not None:
            if user_input[CONF_FROM_AREA] == user_input[CONF_TO_AREA]:
                errors[CONF_TO_AREA] = "same_area"
            else:
                self._from = areas[user_input[CONF_FROM_AREA]]
                self._to = areas[user_input[CONF_TO_AREA]]
                return await self.async_step_commute()  # type: ignore[no-any-return]
        schema = probatio.Schema(
            {
                probatio.Required(CONF_FROM_AREA): self._area_selector(),
                probatio.Required(CONF_TO_AREA): self._area_selector(),
            }
        )
        return self.async_show_form(
            step_id="existing_areas", data_schema=schema, errors=errors
        )

    async def async_step_new_area(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose where the commute starts; the destination is added next."""
        if user_input is not None:
            self._from = self._areas()[user_input[CONF_FROM_AREA]]
            return await self.async_step_area()  # type: ignore[no-any-return]
        schema = probatio.Schema(
            {probatio.Required(CONF_FROM_AREA): self._area_selector()}
        )
        return self.async_show_form(step_id="new_area", data_schema=schema)

    async def _async_area_done(
        self, data: dict[str, Any], title: str, unique_id: str
    ) -> SubentryFlowResult:
        """Add the new destination area, then continue with the commute."""
        await self._async_create_zones()
        self.hass.config_entries.async_add_subentry(
            self._get_entry(),
            ConfigSubentry(
                data=MappingProxyType(data),
                subentry_type=SUBENTRY_AREA,
                title=title,
                unique_id=unique_id,
            ),
        )
        return await self.async_step_commute()  # type: ignore[no-any-return]

    async def _async_commute_done(
        self, data: dict[str, Any], title: str
    ) -> SubentryFlowResult:
        return self.async_create_entry(title=title, data=data)


class ItemSubentryFlow(_EntrySubentryFlow):
    """Add a single item of any type."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._resource = Resource.ROAD_CONDITIONS
        self._choices: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the type of item."""
        if user_input is not None:
            self._resource = Resource(user_input[CONF_RESOURCE])
            return await self.async_step_pick()
        schema = probatio.Schema(
            {
                probatio.Required(CONF_RESOURCE): SelectSelector(
                    SelectSelectorConfig(
                        options=[r.value for r in ITEM_RESOURCES],
                        mode=SelectSelectorMode.LIST,
                        translation_key="data_types",
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the item, nearest to home first."""
        if user_input is not None:
            item_id = user_input[CONF_ITEM_ID]
            unique_id = f"{self._resource.value}:{item_id}"
            if any(
                s.unique_id == unique_id for s in self._get_entry().subentries.values()
            ):
                return self.async_abort(reason="already_configured")
            data = {CONF_RESOURCE: self._resource.value, CONF_ITEM_ID: item_id}
            _with_ha_area(data, user_input)
            return self.async_create_entry(
                title=self._choices[item_id], data=data, unique_id=unique_id
            )
        client, key = self._client_and_key()
        try:
            items = await async_fetch_resource(self.hass, client, key, self._resource)
        except Ontario511AuthError:
            return self.async_abort(reason="invalid_auth")
        except Ontario511RateLimitError:
            return self.async_abort(reason="rate_limited")
        except Ontario511Error:
            return self.async_abort(reason="cannot_connect")
        home = (self.hass.config.latitude, self.hass.config.longitude)
        ranked = sorted(
            (
                (distance_km(home, item.coordinates), item)
                for item in items.values()
                if item.coordinates is not None
            ),
            key=lambda pair: pair[0],
        )[:MAX_ITEM_CHOICES]
        if not ranked:
            return self.async_abort(reason="no_items")
        self._choices = {item.id: item.name for _, item in ranked}
        schema = probatio.Schema(
            {
                probatio.Required(CONF_ITEM_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=item.id, label=f"{item.name} ({km:.0f} km)"
                            )
                            for km, item in ranked
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                _ha_area_field(None): AreaSelector(),
            }
        )
        return self.async_show_form(step_id="pick", data_schema=schema)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the Home Assistant area of a single item's device."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            data = dict(subentry.data)
            _with_ha_area(data, user_input)
            self._move_devices_if_changed(subentry, data)
            return self.async_update_and_abort(self._get_entry(), subentry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=probatio.Schema(
                {_ha_area_field(subentry.data.get(CONF_HA_AREA)): AreaSelector()}
            ),
        )
