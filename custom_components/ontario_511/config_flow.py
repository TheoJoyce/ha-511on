"""Config flow for Ontario 511.

Initial setup asks for the API key, then a first area, then optionally a
commute. Areas, commutes and single items can be added later as subentries.
The data type checkboxes are only used once, to choose what to generate.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_API_KEY, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    LocationSelector,
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
import probatio

from . import Ontario511ConfigEntry
from .api import (
    Ontario511AuthError,
    Ontario511Client,
    Ontario511Error,
    Ontario511RateLimitError,
    Resource,
)
from .client import create_client, key_id
from .commute import change_segments, plan_commute, route_lines
from .const import (
    CONF_CORRIDOR_KM,
    CONF_END,
    CONF_EVENT_SLOTS,
    CONF_FEEDS,
    CONF_ITEM_ID,
    CONF_ITEMS,
    CONF_LOCATION,
    CONF_PENDING,
    CONF_RADIUS_KM,
    CONF_RESOURCE,
    CONF_SEGMENTS,
    CONF_START,
    CONF_TYPES,
    DEFAULT_AREA_NAME,
    DEFAULT_CORRIDOR_KM,
    DEFAULT_EVENT_SLOTS,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    FAST_IMPORT_TYPES,
    FEED_RESOURCES,
    ITEM_RESOURCES,
    MAX_CORRIDOR_KM,
    MAX_RADIUS_KM,
    MIN_CORRIDOR_KM,
    MIN_RADIUS_KM,
    SUBENTRY_AREA,
    SUBENTRY_COMMUTE,
    SUBENTRY_ITEM,
    VALIDATION_RESOURCE,
)
from .coordinator import async_fetch_resource
from .geo import distance_km
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
MAX_EVENT_SLOTS = 50
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


def _slots_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=0, max=MAX_EVENT_SLOTS, step=1, mode=NumberSelectorMode.BOX
        )
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
    except Ontario511Error:
        return {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unexpected error validating the API key")
        return {"base": "unknown"}
    return {}


class _SetupSteps:
    """Area and commute steps shared by initial setup and subentry flows.

    Subclasses provide the API client and decide what happens once an area or
    commute is complete.
    """

    hass: HomeAssistant
    _draft: dict[str, Any]
    _draft_kind: str
    _type_count: int

    async_show_form: Any

    def _client_and_key(self) -> tuple[Ontario511Client, str]:
        raise NotImplementedError

    async def _async_area_done(self, data: dict[str, Any], title: str) -> Any:
        raise NotImplementedError

    async def _async_commute_done(self, data: dict[str, Any], title: str) -> Any:
        raise NotImplementedError

    async def async_step_area(self, user_input: dict[str, Any] | None = None) -> Any:
        """Define an area: where, how far, and what to generate."""
        errors: dict[str, str] = {}
        if user_input is not None:
            radius_km = round(float(user_input[CONF_RADIUS_KM]), 1)
            if not user_input[CONF_TYPES]:
                errors[CONF_TYPES] = "no_types"
            else:
                feeds, pending = _split_types(user_input[CONF_TYPES])
                location = user_input[CONF_LOCATION]
                self._draft_kind = SUBENTRY_AREA
                self._type_count = len(user_input[CONF_TYPES])
                self._draft = {
                    CONF_NAME: user_input[CONF_NAME].strip() or DEFAULT_AREA_NAME,
                    CONF_LOCATION: {
                        "latitude": location["latitude"],
                        "longitude": location["longitude"],
                    },
                    CONF_RADIUS_KM: radius_km,
                    CONF_FEEDS: feeds,
                    CONF_PENDING: pending,
                    CONF_ITEMS: {},
                    CONF_EVENT_SLOTS: int(user_input[CONF_EVENT_SLOTS]),
                }
                return await self._async_next_after_draft()
        defaults = user_input or {
            CONF_NAME: DEFAULT_AREA_NAME,
            CONF_LOCATION: _home(self.hass),
            CONF_RADIUS_KM: DEFAULT_RADIUS_KM,
            CONF_TYPES: DEFAULT_TYPES,
            CONF_EVENT_SLOTS: DEFAULT_EVENT_SLOTS,
        }
        schema = probatio.Schema(
            {
                probatio.Required(CONF_NAME, default=defaults[CONF_NAME]): str,
                probatio.Required(
                    CONF_LOCATION, default=defaults[CONF_LOCATION]
                ): LocationSelector(),
                probatio.Required(
                    CONF_RADIUS_KM, default=defaults[CONF_RADIUS_KM]
                ): _km_selector(MIN_RADIUS_KM, MAX_RADIUS_KM),
                probatio.Required(
                    CONF_TYPES, default=defaults[CONF_TYPES]
                ): _types_selector(TYPE_OPTIONS),
                probatio.Required(
                    CONF_EVENT_SLOTS, default=defaults[CONF_EVENT_SLOTS]
                ): _slots_selector(),
            }
        )
        return self.async_show_form(
            step_id="area",
            data_schema=schema,
            errors=errors,
            description_placeholders={"max_radius": str(int(MAX_RADIUS_KM))},
        )

    async def async_step_commute(self, user_input: dict[str, Any] | None = None) -> Any:
        """Define a commute; its routes are worked out automatically."""
        errors: dict[str, str] = {}
        if user_input is not None:
            start, end = user_input[CONF_START], user_input[CONF_END]
            if distance_km(_point(start), _point(end)) < 1:
                errors[CONF_END] = "too_close"
            elif not user_input[CONF_TYPES]:
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
                    plan = await self.hass.async_add_executor_job(
                        plan_commute,
                        [s for s in segments.values() if isinstance(s, RoadSegment)],
                        _point(start),
                        _point(end),
                    )
                    feeds, pending = _split_types(user_input[CONF_TYPES])
                    self._draft_kind = SUBENTRY_COMMUTE
                    self._type_count = len(user_input[CONF_TYPES])
                    self._draft = {
                        CONF_NAME: user_input[CONF_NAME].strip(),
                        CONF_START: {k: start[k] for k in ("latitude", "longitude")},
                        CONF_END: {k: end[k] for k in ("latitude", "longitude")},
                        CONF_CORRIDOR_KM: round(float(user_input[CONF_CORRIDOR_KM]), 1),
                        CONF_FEEDS: feeds,
                        CONF_PENDING: pending,
                        CONF_ITEMS: {},
                        CONF_EVENT_SLOTS: int(user_input[CONF_EVENT_SLOTS]),
                        **plan,
                    }
                    return await self._async_next_after_draft()
        defaults = user_input or {
            CONF_NAME: "",
            CONF_START: _home(self.hass),
            CONF_END: _home(self.hass),
            CONF_CORRIDOR_KM: DEFAULT_CORRIDOR_KM,
            CONF_TYPES: DEFAULT_TYPES,
            CONF_EVENT_SLOTS: DEFAULT_EVENT_SLOTS,
        }
        schema = probatio.Schema(
            {
                probatio.Required(CONF_NAME, default=defaults[CONF_NAME]): str,
                probatio.Required(
                    CONF_START, default=defaults[CONF_START]
                ): LocationSelector(),
                probatio.Required(
                    CONF_END, default=defaults[CONF_END]
                ): LocationSelector(),
                probatio.Required(
                    CONF_CORRIDOR_KM, default=defaults[CONF_CORRIDOR_KM]
                ): _km_selector(MIN_CORRIDOR_KM, MAX_CORRIDOR_KM),
                probatio.Required(
                    CONF_TYPES, default=defaults[CONF_TYPES]
                ): _types_selector(TYPE_OPTIONS),
                probatio.Required(
                    CONF_EVENT_SLOTS, default=defaults[CONF_EVENT_SLOTS]
                ): _slots_selector(),
            }
        )
        return self.async_show_form(
            step_id="commute", data_schema=schema, errors=errors
        )

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
            return await self._async_area_done(data, title)
        return await self._async_commute_done(data, title)


class Ontario511ConfigFlow(ConfigFlow, _SetupSteps, domain=DOMAIN):
    """Handle a config flow for Ontario 511."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        self._api_key = ""
        self._subentries: list[ConfigSubentryData] = []
        self._draft = {}
        self._draft_kind = SUBENTRY_AREA
        self._type_count = 0

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
        self, data: dict[str, Any], title: str
    ) -> ConfigFlowResult:
        self._subentries.append(
            ConfigSubentryData(
                data=data, subentry_type=SUBENTRY_AREA, title=title, unique_id=None
            )
        )
        return await self.async_step_add_commute()

    async def async_step_add_commute(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer to add a commute now or finish."""
        return self.async_show_menu(
            step_id="add_commute", menu_options=["commute", "finish"]
        )

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
        if user_input is not None:
            new_data = {
                **subentry.data,
                CONF_FEEDS: user_input[CONF_FEEDS],
                CONF_EVENT_SLOTS: int(user_input[CONF_EVENT_SLOTS]),
            }
            if is_commute:
                new_data[CONF_CORRIDOR_KM] = round(
                    float(user_input[CONF_CORRIDOR_KM]), 1
                )
                new_data.update(
                    change_segments(subentry.data, user_input[CONF_SEGMENTS], segments)
                )
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
            probatio.Required(
                CONF_EVENT_SLOTS,
                default=subentry.data.get(CONF_EVENT_SLOTS, DEFAULT_EVENT_SLOTS),
            ): _slots_selector(),
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
        return self.async_show_form(
            step_id="reconfigure_commute" if is_commute else "reconfigure",
            data_schema=probatio.Schema(fields),
        )

    async def async_step_reconfigure_commute(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle the commute version of the reconfigure form."""
        return await self.async_step_reconfigure(user_input)


class AreaSubentryFlow(_EntrySubentryFlow, _SetupSteps):
    """Add or change an area."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._draft = {}
        self._draft_kind = SUBENTRY_AREA
        self._type_count = 0

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add an area."""
        return await self.async_step_area(user_input)  # type: ignore[no-any-return]

    async def _async_area_done(
        self, data: dict[str, Any], title: str
    ) -> SubentryFlowResult:
        return self.async_create_entry(title=title, data=data)


class CommuteSubentryFlow(_EntrySubentryFlow, _SetupSteps):
    """Add or change a commute."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._draft = {}
        self._draft_kind = SUBENTRY_COMMUTE
        self._type_count = 0

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a commute."""
        return await self.async_step_commute(user_input)  # type: ignore[no-any-return]

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
            return self.async_create_entry(
                title=self._choices[item_id],
                data={CONF_RESOURCE: self._resource.value, CONF_ITEM_ID: item_id},
                unique_id=unique_id,
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
                )
            }
        )
        return self.async_show_form(step_id="pick", data_schema=schema)
