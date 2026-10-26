# Agent Instructions

> Adapted from [`AGENTS.md` in home-assistant/core](https://github.com/home-assistant/core/blob/7c3a98ae6f4454d65500438b2fb503363fcf490a/AGENTS.md), licensed under the [Apache License 2.0](LICENSE). Modified for this project: rewritten for a custom integration (uv commands, no Core scripts or PR template), and sections on Ontario 511 compliance, rate limits, architecture, and versioning and releases added.

This repository contains the Ontario 511 integration (repository `ha-511on`, domain `ontario_511`), a community-maintained Home Assistant custom integration for Ontario 511 traffic data. It is a custom integration, not part of Home Assistant Core.

## Ontario 511 compliance

These rules come from the Ontario 511 [API documentation](https://511on.ca/developers/doc), its [Developer's Access Agreement](https://511on.ca/developers/resources) and the [Open Government Licence – Ontario](https://www.ontario.ca/page/open-government-licence-ontario). They are not negotiable.

- **Keys:** every user supplies their own API key. Never hardcode, ship, log or commit a key. Keys live in config entry data. For local development, use `.env` (`ONTARIO_511_API_KEY`), which is gitignored. Redact keys from diagnostics, logs and request URLs in error messages.
- **Rate limit:** see [Rate limits](#rate-limits) below. Never add an option that bypasses or raises the limit.
- **Polling:** design schedules to stay well below the limit, not run at it. Poll static resources (rest areas, roundabouts, service centres and so on) rarely, and live resources (events, alerts, road conditions) more often. Users cannot configure polling intervals.
- **Attribution:** every entity sets `_attr_attribution` to `ATTRIBUTION` from `const.py`. The README keeps the licence link and a link to https://511on.ca.
- **No endorsement:** never use the Ontario 511, MTO or Government of Ontario logos in brand assets or docs, and never imply that the project is official or endorsed.
- **Naming:** wherever the integration is presented as "Ontario 511" (README title, integration listings, docs), state that it is community-maintained and not affiliated with or endorsed by Ontario 511, MTO or the Government of Ontario. Keep that notice at the top of the README.
- **Disclaimer:** keep the "as is" data disclaimer in the README.
- **Tests:** tests never call the live API. Use recorded JSON fixtures.

## Address search (OpenStreetMap Nominatim)

`geocode.py` looks up addresses typed in the area form. The [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) applies to the integration as a whole, so these rules are not negotiable:

- Never add autocomplete, periodic, background or bulk lookups. Only a user submitting a form may trigger one.
- Keep the one-request-per-second spacing, the result cache, and the integration's own User-Agent.
- Keep the remote switch: `geocoding.json` on `main` must stay in the repository. Lookups must stay refused when it says off or can't be read. To switch address search off for everyone (for example if OpenStreetMap asks), set `"enabled": false` there.
- Keep the attribution and the note that the address is sent to OpenStreetMap on the form, and in the README.
- In development, `scripts/develop` points lookups at the fake API (`ONTARIO_511_GEOCODING_BASE`), so test addresses never reach OpenStreetMap. Tests mock both URLs.

## Rate limits

- **What the docs say:** the [API documentation](https://511on.ca/developers/doc) states one limit for the whole API: "Throttling is enabled. Ten calls every 60 seconds." None of the 16 endpoint pages documents a different limit (checked October 2026). Re-check the docs when adding a resource.
- **Scope:** the docs don't say whether the limit is counted per key or per IP address. The integration therefore uses **one `RateLimiter` for the whole Home Assistant instance** (`client.get_rate_limiter`), shared by every config entry and every key, and by the config flow.
- **The limiter** (`api.RateLimiter`) is a sliding window of 10 calls per 61 seconds (the extra second absorbs clock and network skew). `acquire()` waits for a free slot instead of failing. `Ontario511Client.async_get` acquires a slot before every request, so no code path can skip it.
- **Polling budget:** fixed intervals live in `const.UPDATE_INTERVALS`. Their combined calls per minute must stay at or under `const.POLL_BUDGET_PER_MINUTE`, which must stay at or under a fifth of the limit. `tests/test_rate_limits.py` enforces both; when adding a polled resource, give it an interval, a fixture, and keep the budget test passing.
- **Throttled responses:** an HTTP 429 raises `Ontario511RateLimitError`. Coordinators turn it into `UpdateFailed` and wait for the next scheduled poll; never retry immediately.
- **Development key:** the key `dev-cache` (`client.DEV_API_KEY`) makes `create_client` return a `DevCacheClient`, which reads `.dev-cache/api/` next to `custom_components/` and never calls the API, so it doesn't use the limiter. Without that directory the key is refused (`dev_cache_missing`). Never let it fall back to the real API. Tests point `client.DEV_CACHE_DIR` at copies of the fixtures, never at `.dev-cache`.
- **Live calls during development:** anything that calls the real API must go through `RateLimiter` (use `Ontario511Client`). Record fixtures with `uv run python -m scripts.record_fixtures OUT_DIR [RESOURCE ...]`: all 16 resources take about a minute because of the limiter. Don't run it while a development Home Assistant instance uses the same key, because they don't share a limiter.
- **Fixtures vs the dev cache:** `tests/fixtures/` is the source of truth for tests. It holds the documented sample response for every resource, plus real items where live data differs from the docs (see `tests/fixtures/README.md`). `.dev-cache/api/` is a gitignored local copy of full responses from every resource, for browsing real data during development; it may be stale and tests must never read it. Refresh it with `uv run python -m scripts.record_fixtures .dev-cache/api`.
- **Testing the limiter:** tests freeze time, which also freezes `time.monotonic`, so an unpatched limiter would wait forever once more than 10 resources are in use. `tests/conftest.py` installs the instance-wide limiter with a `SimulatedClock` in every test: the limit is still enforced, the wait is simulated and recorded (`limiter_clock.waits`). Unit tests inject their own fake clock. Integration tests use `aioclient_mock` with the real client, and assert on `limiter.available`, `call_count` and the recorded waits.
- **Coordinators only poll while something listens.** Tests of polling need entities (or a listener) attached.
- **Wait for background tasks in tests.** Initial data loads and scheduled coordinator refreshes run as background tasks, and parsing runs in the executor. After setting up an entry or advancing time, use `await hass.async_block_till_done(wait_background_tasks=True)`; a plain `async_block_till_done()` can return before the data arrives and makes tests flaky.

## Architecture

See `docs/design.md` for the data model (areas, commutes, items, devices and entities) and `docs/api-mapping.md` for where every API field ends up. Keep `api-mapping.md` up to date when a field's handling changes.

- `api.py`: the API client, `Resource` enum, errors and `RateLimiter`. No Home Assistant imports. Every resource is under `/api/v2/get/` except `roadconditions`, which is under `/api/v3/get/`. Error messages never contain the request URL, because it carries the key.
- `client.py`: Home Assistant glue: the instance-wide limiter and client creation (shared aiohttp session; `lang=fr` when Home Assistant's language is French).
- `policy.py`: **the single reference for data freshness.** One `ResourcePolicy` per resource: category, background refresh interval, and freshness window. Don't hardcode intervals anywhere else.
- `coordinator.py`: `ResourceCoordinator`, one per resource in use. It caches the whole parsed response; every entity of that resource reads from it. A forced refresh ("Update entity") reuses the cache inside the freshness window and is debounced. Parsing runs in the executor, because decoding road polylines is slow on small hardware.
- `models.py`: frozen dataclasses for all 16 resources, all sharing `id`, `name`, `coordinates` and `region`. Resources without an `Id` field get a derived ID from stable fields. Coordinates outside Ontario's bounding box are treated as missing. Road segment polylines are thinned to about one point per 250 m to save memory.
- `conditions.py`: ranks free-text road conditions; unrecognised text is unknown (never assumed safe) and logged once.
- `geo.py`: polyline decoding and distances. `scope.py`: which items fall in a radius or commute corridor, and attaching events to their nearest road segment (within 2 km).
- `__init__.py`: works out the resources in use from the subentries, creates their coordinators, waits for one (to check the key and connection) and loads the rest in the background, runs the one-time generation for new areas and commutes, and handles deleting an item's device.
- `generation.py`: chooses generated items (commute corridor first, then nearest to an area's centre, within per-type caps). Pure functions; heavy work runs in the executor.
- `routing.py`: routes commutes over the road segment network (segments split where others join them partway; Dijkstra; up to three routes within 30% of the best). `commute.py` plans a commute and reads its stored route: the segments used and the stretches actually driven, so corridors follow the trip rather than whole segments. Trips where local roads beat any highway route use the straight start-to-end line.
- `config_flow.py`: initial setup (key, first area, optional commute) and the area, commute and item subentry flows.
- `client.py` also holds the instance-wide response cache, so reloading an entry (which happens whenever a subentry changes) reuses fetched data.
- `feeds.py`: `FeedIndex`, rebuilt only when data changes: active events and construction attached to their road segment, and each area's or commute's scope (events, construction, segments, regions, alerts).
- `entity.py`: base classes. `ItemEntity` (one item's device) and `SummaryEntity` (an area's or commute's summary device); `extra_coordinators` lets an entity update when any of several resources changes. Every entity is added with its subentry's `config_subentry_id`.
- Platforms: `sensor.py`, `binary_sensor.py`, `camera.py` (one entity per view, images cached per `policy.CAMERA_IMAGE_CACHE`, views added as the camera list loads) and `event.py` (fires for IDs that weren't there at the previous update; the mixin must come first in the bases).

## Versioning and releases

- Versions follow [SemVer](https://semver.org/). Pre-releases use `X.Y.Z-alpha.N`, `X.Y.Z-beta.N` and `X.Y.Z-rc.N`. HACS compares versions with AwesomeVersion, which orders these correctly.
- Each release is developed on a branch named `dev/<version>`, for example `dev/0.1.0-alpha.1`. On that branch, set the same version (without a `v`) in `custom_components/ontario_511/manifest.json` and `pyproject.toml`; `tests/test_manifest.py` checks they match.
- `main` is the default branch. Never commit directly to it; changes reach it only by merging a reviewed pull request.
- `main` is protected by the GitHub ruleset "Protect main": changes only through a pull request with resolved conversations, the `checks`, `hassfest` and `hacs` checks passing on an up-to-date branch, and no force pushes or deletion. No approvals are required, because GitHub doesn't let the sole maintainer approve their own pull request. The required checks are matched by job name: if a workflow job is renamed, update the ruleset too, or merges will wait for a check that never reports.

### Workflow

1. **Branch:** create `dev/<version>` from `main` and bump the version.
2. **Push every commit** to the branch on GitHub.
3. **Draft PR:** if the branch has no pull request yet, open a **draft** PR into `main` using the PR template. Fill in the version, the goals, and a task checklist for the release.
4. **Work in the PR:** all commits, reviews and checklists for that version live in its PR. Tick tasks as they're done and add new ones as they come up. Mark the PR ready for review when the tasks are done.
5. **Merge:** a human reviews and approves the PR, then merges it. A merge means that version is official.
6. **Delete the branch:** the branch is always deleted after merging. GitHub does this automatically (the repository has "Automatically delete head branches" turned on).
7. **Release:** tag the merge commit `v<version>` and publish a GitHub release from the tag. Mark alpha, beta and rc versions as pre-releases: HACS only offers pre-releases to users who turn on beta versions. `.github/workflows/release.yml` fails if the tag doesn't match the manifest version or the pre-release flag is wrong.

## Git Commit Guidelines

- **Do NOT amend, squash, or rebase commits that have already been pushed to the PR branch after the PR is opened** - Reviewers need to follow the commit history, as well as see what changed since their last review

## Pull Requests

- If the repository has a PR template, use it and never remove anything from it.
- Do not remove checkboxes that are not checked. Leave all unchecked checkboxes in place so reviewers can see which options were not selected.

## Development Commands

- Run `uv sync` to set up the virtual environment with all development dependencies. If uv reports that no download was found for the required Python version, uv is outdated; upgrade it and run `uv sync` again.
- Use `uv run` so the project's virtual environment and Python version are used.
- After finishing a code session, run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` and `uv run pytest`, and `npm test` if the map card changed (`npm ci --ignore-scripts` once first). CI also runs hassfest (`hassfest.yml`) and HACS validation (`hacs.yml`) as separate workflows.
- **Local Home Assistant:** `scripts/develop` runs a dev instance at http://localhost:8123 against a fake Ontario 511 API (`scripts/fake_511.py`) that serves `.dev-cache/api/`, enforces the real rate limit, and accepts data injection. See `docs/development.md`. The integration only talks to the fake API when `ONTARIO_511_API_BASE` is set; normal installs never set it.
- Run hassfest locally with the same image CI uses: `docker run --rm -v "$PWD"://github/workspace ghcr.io/home-assistant/hassfest`. Its current release rejects the manifest `categories` field, so leave it out until hassfest accepts it; HACS only warns that it's missing.
- Home Assistant 2026.10 uses `probatio` in place of `voluptuous`; import `probatio` the way Core does.
- **Icons:** hassfest doesn't check icon names, and a name that doesn't exist shows no icon. Run `uv run --group run python -m scripts.check_icons` after changing `icons.json`. Give each entity its own icon, with state icons where the state changes what matters (for example a snowflake for snow-covered roads).
- **Follow Home Assistant's conventions for states and naming; check the developer docs (or Home Assistant's source) before choosing.** Where Ontario 511's values don't map onto a Home Assistant convention, use the Home Assistant one. In particular, when the feed has no value (road conditions "No Report", ferries "No Status Available"), return `None`: Home Assistant shows its standard, translated Unknown state and skips the enum option check. Never add an enum option for "no value", and never use `unknown` or `unavailable` as option names. Keep the feed's raw text in an attribute (such as `reported`) instead.
- **Full data, statuses rather than yes/no:** expose every API field (see `docs/api-mapping.md`, and keep it up to date). A value with more than two states is an enum status sensor, never a binary sensor; a combined status (such as Road condition including drifting snow) keeps each part as its own entity. Disable granular and duplicate entities by default (`entity_registry_enabled_default=False`) instead of leaving data out. Events and alerts carry every field through `entity.event_details` and `entity.alert_details`.
- **Map card:** `frontend/ontario-511-map-card.js` has no build step. `npm test` runs its unit tests (`tests/frontend/`, Node's test runner with happy-dom and a fake MapLibre beside a copy of the card). `npm run test:e2e` checks it end to end against a running `scripts/develop`: it uses the development key's entry (adding an area and commute to an existing one, and removing only those, or creating one and removing it), adds the card to a dashboard and checks it in headless Chromium (screenshots in `config/e2e/`). Run both after changing the card. Home Assistant 2026.10 replaces `window.customElements` after loading extra modules, so the card registers only once the frontend's `home-assistant` element exists.
- Pin GitHub Actions to tags that exist. Some actions (for example `astral-sh/setup-uv`) no longer publish major-only tags such as `v10`; use the full version.

## Python Syntax Notes

- Home Assistant officially supports Python 3.14 as its minimum version, and this project requires Python 3.14.2 or later. Do not flag syntax or features that require Python 3.14 as issues, and do not suggest workarounds for older Python versions.
- Python 3.14 explicitly allows `except TypeA, TypeB:` without parentheses. Never flag this as an issue.
- Python 3.14 evaluates annotations lazily (PEP 649). Forward references in annotations do not need to be quoted. Do not flag unquoted forward references in annotations as issues.

## Testing

- Use `uv run pytest` to run tests. Tests use `pytest-homeassistant-custom-component`.
- Custom integrations have no `strings.json` or translation script. Edit `custom_components/ontario_511/translations/en.json` directly.
- When writing or modifying tests, ensure all test function parameters have type annotations.
- Prefer concrete types (for example, `HomeAssistant`, `MockConfigEntry`, etc.) over `Any`.
- Prefer `@pytest.mark.usefixtures` over arguments, if the argument is not going to be used.
- Avoid using conditions/branching in tests. Instead, either split tests or adjust the test parametrization to cover all cases without branching.
- If multiple tests share most of their code, use `pytest.mark.parametrize` to merge them into a single parameterized test instead of duplicating the body. Use `pytest.param` with an `id` parameter to name the test cases clearly.
- We use Syrupy for snapshot testing. Leverage `.ambr` snapshots instead of repetitive and exhaustive generation of test data within Python code itself.
- Hardcoded `entity_id`s in tests are fine. If the same one is repeated, use a constant.

## Good practices

- Integrations in home-assistant/core with Platinum or Gold level in the Integration Quality Scale reflect a high standard of code quality and maintainability. When looking for examples of something, these are good places to start. The level is indicated in the integration's `manifest.json`.
- When reviewing entity actions, do not suggest extra defensive checks for input fields that are already validated by Home Assistant's service/action schemas and entity selection filters. Suggest additional guards only when data bypasses those validators or is transformed into a less-safe form.
- When validation guarantees a dict key exists, prefer direct key access (`data["key"]`) instead of `.get("key")` so contract violations are surfaced instead of silently masked.
- Keep comments concise. Prefer one short line stating the non-obvious constraint, or no comment at all.
- Do not add comments that just restate the code on the following line(s) (e.g. `# Check if initialized` above `if self.initialized:`). Comments should only explain why (non-obvious constraints, surprising behavior, or workarounds), never what. Never add comments that justify a change by referencing what the code looked like before. Comments in tests that explain why a function call or assertion is made are ok.
- Do not add section or divider comments (e.g. `# --- XYZ Triggers ---`) inside or outside of functions, since those can easily become stale and be misleading.
- When catching exceptions, try-clauses should be as small as possible, i.e. avoid wrapping large blocks of code in a try-clause, and avoid catching exceptions from functions that are not expected to raise them.
- Asserts that exist only to narrow types for the type checker must be placed behind an `if TYPE_CHECKING:` guard, so they are not executed at runtime.
- Sensitive service actions, i.e. those that can change configuration or have security implications, should require an admin user. Register them with the `async_register_admin_service` service helper, which checks this for you.

## AI policy

Autonomous contributions are not accepted: a human must review, understand, and be able to explain every change before it is merged. Agents may push commits to `dev/*` branches and open draft pull requests as described in [Workflow](#workflow), but never merge, approve, mark a pull request ready for review, or publish a release without the user's go-ahead. Do not open issues, and do not post comments on behalf of a user without their review.
