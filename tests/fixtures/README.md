# Test fixtures

API responses the tests serve through `aioclient_mock`, one file per resource, named after the resource's path segment. Tests never call the live API; these files are the source of truth.

- **Documented samples:** every file contains the example response from that resource's page in the [API documentation](https://511on.ca/developers/doc), so every documented field is covered. The documented alerts sample closes one string with a typographic quote (`”`); that one character is replaced with `"` so the file is valid JSON. Whitespace inside alert strings is collapsed.
- **Real items** (recorded October 2026 with `scripts/record_fixtures.py`) are added where live data behaves differently from the docs:
  - `event.json`: seven live events chosen for the tests' frozen time (`FROZEN_NOW` in `conftest.py`). Three nearby events are active (two full closures and a collision), two nearby events are planned, one has a flipped longitude and has ended, and one is about 1,300 km away. The two documented samples ended in 2018.
  - `roadconditions.json`: a segment reporting "No Report" and one reporting "Bare and wet road".
  - `ferryterminals.json`: a terminal whose status uses the undocumented casing "Not In Service".
  - `seasonalloadapi.json`: two segments sharing the same name and route, and a status with a trailing space.

Live data had no alerts when recorded, so `alerts.json` is only the documented sample.

For browsing complete, current-ish responses during development, use the gitignored `.dev-cache/api/` (see `AGENTS.md`). Tests must not read it.
