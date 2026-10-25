# Agent Instructions

> Adapted from [`AGENTS.md` in home-assistant/core](https://github.com/home-assistant/core/blob/7c3a98ae6f4454d65500438b2fb503363fcf490a/AGENTS.md), licensed under the [Apache License 2.0](LICENSE). Modified for this project: rewritten for a custom integration (uv commands, no Core scripts or PR template), and a section on Ontario 511 compliance added.

This repository contains the Ontario 511 integration (repository `ha-511on`, domain `ontario_511`), a community-maintained Home Assistant custom integration for Ontario 511 traffic data. It is a custom integration, not part of Home Assistant Core.

## Ontario 511 compliance

These rules come from the Ontario 511 [API documentation](https://511on.ca/developers/doc), its [Developer's Access Agreement](https://511on.ca/developers/resources) and the [Open Government Licence – Ontario](https://www.ontario.ca/page/open-government-licence-ontario). They are not negotiable.

- **Keys:** every user supplies their own API key. Never hardcode, ship, log or commit a key. Keys live in config entry data. For local development, use `.env` (`ONTARIO_511_API_KEY`), which is gitignored. Redact keys from diagnostics, logs and request URLs in error messages.
- **Rate limit:** Ontario 511 allows 10 calls every 60 seconds per key. Enforce this in code with one limiter per key, shared across every config entry that uses that key. The limiter covers every call, including config flow validation. Never add an option that bypasses or raises the limit.
- **Polling:** design schedules to stay well below the limit, not run at it. Poll static resources (rest areas, roundabouts, service centres and so on) rarely, and live resources (events, alerts, road conditions) more often. Users cannot configure polling intervals.
- **Attribution:** every entity sets `_attr_attribution` to `ATTRIBUTION` from `const.py`. The README keeps the licence link and a link to https://511on.ca.
- **No endorsement:** never use the Ontario 511, MTO or Government of Ontario logos in brand assets or docs, and never imply that the project is official or endorsed.
- **Naming:** wherever the integration is presented as "Ontario 511" (README title, integration listings, docs), state that it is community-maintained and not affiliated with or endorsed by Ontario 511, MTO or the Government of Ontario. Keep that notice at the top of the README.
- **Disclaimer:** keep the "as is" data disclaimer in the README.
- **Tests:** tests never call the live API. Use recorded JSON fixtures.

## Git Commit Guidelines

- **Do NOT amend, squash, or rebase commits that have already been pushed to the PR branch after the PR is opened** - Reviewers need to follow the commit history, as well as see what changed since their last review

## Pull Requests

- If the repository has a PR template, use it and never remove anything from it.
- Do not remove checkboxes that are not checked. Leave all unchecked checkboxes in place so reviewers can see which options were not selected.

## Development Commands

- Run `uv sync` to set up the virtual environment with all development dependencies. If uv reports that no download was found for the required Python version, uv is outdated; upgrade it and run `uv sync` again.
- Use `uv run` so the project's virtual environment and Python version are used.
- After finishing a code session, run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` and `uv run pytest`. CI also runs hassfest (`hassfest.yml`) and HACS validation (`hacs.yml`) as separate workflows.

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

Autonomous contributions are not accepted: a human must review, understand, and be able to explain every change before it is submitted. Do not open issues or pull requests autonomously, and do not post comments on behalf of a user without their review.
