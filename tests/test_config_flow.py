"""Tests for the Ontario 511 config flow."""

from __future__ import annotations

import hashlib

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.ontario_511.client import get_rate_limiter
from custom_components.ontario_511.const import (
    CONF_RADIUS,
    DOMAIN,
    VALIDATION_RESOURCE,
)

from .conftest import API_KEY

INVALID_KEY_BODY = "<Error><Message>Invalid Key</Message></Error>"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A valid key creates an entry after one rate-limited check."""
    aioclient_mock.get(VALIDATION_RESOURCE.url, json=[])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: f"  {API_KEY} "}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Ontario 511"
    assert result["data"] == {CONF_API_KEY: API_KEY}
    assert result["result"].unique_id == hashlib.sha256(API_KEY.encode()).hexdigest()
    assert aioclient_mock.call_count == 1
    limiter = get_rate_limiter(hass)
    assert limiter.available == limiter.calls - 1


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        pytest.param(
            {"status": 400, "text": INVALID_KEY_BODY}, "invalid_auth", id="auth"
        ),
        pytest.param({"status": 429}, "rate_limited", id="rate_limited"),
        pytest.param({"status": 503}, "cannot_connect", id="cannot_connect"),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow_errors_recover(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_kwargs: dict[str, object],
    error: str,
) -> None:
    """Errors are shown, and a later valid attempt succeeds."""
    aioclient_mock.get(VALIDATION_RESOURCE.url, **mock_kwargs)  # type: ignore[arg-type]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(VALIDATION_RESOURCE.url, json=[])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unexpected_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unexpected exception maps to the unknown error."""
    aioclient_mock.get(VALIDATION_RESOURCE.url, exc=RuntimeError("boom"))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["errors"] == {"base": "unknown"}


async def test_duplicate_key_aborts_without_api_call(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A key that is already configured is rejected before any call."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert aioclient_mock.call_count == 0


async def test_reauth_flow(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Reauthentication stores the new key and its unique ID."""
    new_key = "replacement-key-555"
    result = await init_integration.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    aioclient_mock.clear_requests()
    aioclient_mock.get(VALIDATION_RESOURCE.url, status=400, text=INVALID_KEY_BODY)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(VALIDATION_RESOURCE.url, json=[])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: new_key}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert init_integration.data[CONF_API_KEY] == new_key
    assert init_integration.unique_id == hashlib.sha256(new_key.encode()).hexdigest()


async def test_options_flow(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The radius can be changed."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_RADIUS: 5}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {CONF_RADIUS: 5}
