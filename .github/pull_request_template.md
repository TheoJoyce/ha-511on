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
- [ ] Tried in a real Home Assistant instance

## After merging

- [ ] Branch deleted
- [ ] Tag `v<version>` on the merge commit
- [ ] GitHub release published from the tag (marked pre-release for alpha, beta and rc)
