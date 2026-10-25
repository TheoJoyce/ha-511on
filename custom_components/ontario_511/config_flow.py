"""Config flow for Ontario 511."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import probatio

from . import Ontario511ConfigEntry
from .api import Ontario511AuthError, Ontario511Error, Ontario511RateLimitError
from .client import create_client
from .const import (
    CONF_RADIUS,
    DEFAULT_RADIUS_KM,
    DOMAIN,
    MAX_RADIUS_KM,
    MIN_RADIUS_KM,
    VALIDATION_RESOURCE,
)

_LOGGER = logging.getLogger(__name__)

KEY_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


def _key_id(api_key: str) -> str:
    """Return a stable ID for a key without storing the key in the unique ID."""
    return hashlib.sha256(api_key.encode()).hexdigest()


class Ontario511ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ontario 511."""

    VERSION = 1

    async def _async_validate(self, api_key: str) -> dict[str, str]:
        """Check the key with one rate-limited API call."""
        try:
            await create_client(self.hass, api_key).async_get(VALIDATION_RESOURCE)
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

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            await self.async_set_unique_id(_key_id(api_key))
            self._abort_if_unique_id_configured()
            if not (errors := await self._async_validate(api_key)):
                return self.async_create_entry(
                    title="Ontario 511", data={CONF_API_KEY: api_key}
                )
        return self.async_show_form(
            step_id="user", data_schema=KEY_SCHEMA, errors=errors
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
            if not (errors := await self._async_validate(api_key)):
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    unique_id=_key_id(api_key),
                    data_updates={CONF_API_KEY: api_key},
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=KEY_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: Ontario511ConfigEntry,
    ) -> Ontario511OptionsFlow:
        """Return the options flow."""
        return Ontario511OptionsFlow()


class Ontario511OptionsFlow(OptionsFlowWithReload):
    """Handle Ontario 511 options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the radius for nearby events."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_RADIUS, default=DEFAULT_RADIUS_KM
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_RADIUS_KM,
                        max=MAX_RADIUS_KM,
                        step=1,
                        unit_of_measurement="km",
                        mode=NumberSelectorMode.BOX,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )
