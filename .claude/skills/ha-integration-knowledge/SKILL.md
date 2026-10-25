---
name: ha-integration-knowledge
description: Everything you need to know to build, test and review this Home Assistant custom integration. If you're looking at the integration, you must use this as your primary reference.
---

> Adapted from [`ha-integration-knowledge` in home-assistant/core](https://github.com/home-assistant/core/tree/7c3a98ae6f4454d65500438b2fb503363fcf490a/.claude/skills/ha-integration-knowledge), licensed under the [Apache License 2.0](../../../LICENSE). Modified for this project: paths changed to the custom integration layout; Core-only guidance (PyPI library requirement, dependency bump PRs, the device tracker platform, the Core scaffold path) removed or adapted.

## File Locations
- **Integration code**: `./custom_components/ontario_511/`
- **Integration tests**: `./tests/`
- **Test fixtures**: `./tests/fixtures/`

## General guidelines

- Follow the Ontario 511 compliance rules in `AGENTS.md` (keys, rate limit, attribution) before anything else here.
- When looking for examples, prefer integrations in home-assistant/core with the platinum or gold quality scale level first.
- Polling intervals are NOT user-configurable. Never add scan_interval, update_interval, or polling frequency options to config flows or config entries.
- Do NOT allow users to set config entry names in config flows. Names are automatically generated or can be customized later in UI.
- For entity actions and entity services, avoid requesting redundant defensive checks for fields already enforced by Home Assistant validation schemas and entity filters; only request extra guards when values bypass validation or are transformed unsafely.
- When validation guarantees a key is present, prefer direct dictionary indexing (`data["key"]`) over `.get("key")` so invalid assumptions fail fast.
- Core integrations must keep protocol and domain logic in a separate PyPI library. This custom integration keeps its Ontario 511 API client in the repository, but in its own module with no Home Assistant imports, so it stays testable on its own and could be extracted later.
- Do not add entities for data that Home Assistant intentionally only exposes through an action, for example weather forecasts (`weather.get_forecasts`).
- In config flows, map known failures to the matching standard error (for example `invalid_auth`, `cannot_connect`) without logging them. If unexpected exceptions are caught, log them with `_LOGGER.exception()` and map them to `unknown`. Do not mix translated and untranslated text in an error shown to the user.

The following platforms have extra guidelines:
- **Diagnostics**: [`platform-diagnostics.md`](platform-diagnostics.md) for diagnostic data collection
- **Repairs**: [`platform-repairs.md`](platform-repairs.md) for user-actionable repair issues

## Entity platforms

- Ensure `async_added_to_hass()` and `async_will_remove_from_hass()` have symmetrical behavior. For example, if a subscription is created in `async_added_to_hass()`, it should be unsubscribed in `async_will_remove_from_hass()`. Also, if something is torn down in `async_will_remove_from_hass()`, it should be set up in `async_added_to_hass()`.
- Use `_attr_*` attributes instead of properties for values that never change. Define them as class attributes only when the value is shared by all instances.
- When overriding `available`, combine it with the parent's value (`return super().available and ...`).
- Entity base class (e.g. `SensorEntity`, `TrackerEntity`) provide a stable API for child classes to inherit from. Do not suggest redeclaring or duplicating attributes, properties, or methods the base class already provides, and do not add guards against the parent's behavior changing — rely on the base class instead.

## Actions

- Action names and action fields are defined in `<Prefix>Service` and `<Prefix>ServiceArgument` `StrEnum`s, using the integration's usual class prefix. Define them in `services.py`, or in `const.py` when other modules need them.
- The members cover the registered actions and their fields as listed in `services.yaml`. Do not add members for actions that are not registered, such as stale `services.yaml` entries.
- Use the members for action registration, schema keys and `call.data` lookups instead of raw strings or other string constants (whatever their prefix, for example `SERVICE_*`, `ATTR_*` or `CONF_*`). Shared selector fields such as `config_entry_id`, `device_id` and `entity_id` keep using the `homeassistant.const` constants.

## Integration Quality Scale

- Custom integrations are not graded on the quality scale, but its rules promote best practices. Consider them when implementing or reviewing this integration.
- The rules are at https://contributors.home-assistant.io/core/quality-scale/rules/
- Core's template scale file, useful as a checklist: https://github.com/home-assistant/core/blob/dev/script/scaffold/templates/integration/integration/quality_scale.yaml

## Testing Requirements

- Tests should avoid interacting or mocking internal integration details. For more info, see https://contributors.home-assistant.io/core/development/tests/#test-through-home-assistant
- Define shared mocks as fixtures in `conftest.py` so every platform test can reuse them, including a `mock_config_entry`. Give it a `unique_id` when the integration's config entries have one.
- Load substantial or reused mock API data from JSON fixture files with `load_json_object_fixture` or `load_json_array_fixture` from `pytest_homeassistant_custom_component.common` (or their `async_` variants) instead of building it inline with `json.dumps()`.
- Do not manually assert values that a snapshot test already covers.
- When a `Platform` member or an `ATTR_*` constant exists for a value, use it instead of a string literal.
- Patch objects where they are used, not where they are defined.
- To give one test different mock data, parametrize the fixture that provides it with `pytest.mark.parametrize` instead of patching again inside the test. Use `indirect=True` when the fixture reads the value from `request.param`.
