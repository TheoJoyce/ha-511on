# Repairs platform

> Adapted from [`platform-repairs.md` in home-assistant/core](https://github.com/home-assistant/core/blob/7c3a98ae6f4454d65500438b2fb503363fcf490a/.claude/skills/ha-integration-knowledge/platform-repairs.md), licensed under the [Apache License 2.0](../../../LICENSE). Modified for this project: path.

Platform exists as `custom_components/ontario_511/repairs.py`.

- **Actionable Issues Required**: All repair issues must be actionable for end users
- **Issue Content Requirements**:
  - Clearly explain what is happening
  - Provide specific steps users need to take to resolve the issue
  - Use friendly, helpful language
  - Include relevant context (device names, error details, etc.)
- **String Content Must Include**:
  - What the problem is
  - Why it matters
  - Exact steps to resolve (numbered list when multiple steps)
  - What to expect after following the steps
- **Avoid Vague Instructions**: Don't just say "update firmware" - provide specific steps
- **Severity Guidelines**:
  - `CRITICAL`: Reserved for extreme scenarios only
  - `ERROR`: Requires immediate user attention
  - `WARNING`: Indicates future potential breakage
- Only create issues for problems users can potentially resolve
