# Integration Diagnostics

> Adapted from [`platform-diagnostics.md` in home-assistant/core](https://github.com/home-assistant/core/blob/7c3a98ae6f4454d65500438b2fb503363fcf490a/.claude/skills/ha-integration-knowledge/platform-diagnostics.md), licensed under the [Apache License 2.0](../../../LICENSE). Modified for this project: path and API key note.

Platform exists as `custom_components/ontario_511/diagnostics.py`.

- **Required**: Implement diagnostic data collection
- **Security**: Never expose passwords, tokens, or sensitive coordinates. Always redact the Ontario 511 API key.
