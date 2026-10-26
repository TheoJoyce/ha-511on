## Version

<!-- The version this branch releases, e.g. 0.1.0-alpha.1. The branch is dev/<version>. -->

## Goals

<!-- What this version sets out to do. -->

## Tasks

<!-- Tick tasks as they're done; add new ones as they come up. -->

- [ ]

## Checklist

- [ ] Version set in `manifest.json` and `pyproject.toml`
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` and `uv run pytest` pass
- [ ] hassfest and HACS validation pass
- [ ] Polling stays within the budget in `tests/test_rate_limits.py`, and every API call goes through the rate limiter
- [ ] No API key in code, fixtures, logs or diagnostics
- [ ] Every entity has the OGL–Ontario attribution
- [ ] README updated for user-facing changes
- [ ] Release notes written in `docs/releases/<version>.md`
- [ ] Tried in a real Home Assistant instance

## After merging

Merging publishes the release: the branch is deleted, and the Release workflow tags the merge commit `v<version>` and publishes the notes (as a pre-release for alpha, beta and rc).

- [ ] Release workflow succeeded and the release is on GitHub
