---
name: ha-review
description: Reviews code changes in this Home Assistant custom integration and provides constructive feedback. Should be used when a review is requested to provide a consistent review behavior and output format. This skill can be used for code reviews in general, not just for GitHub pull requests.
---

> Adapted from [`ha-review` in home-assistant/core](https://github.com/home-assistant/core/tree/7c3a98ae6f4454d65500438b2fb503363fcf490a/.claude/skills/ha-review), licensed under the [Apache License 2.0](../../../LICENSE). Modified for this project: default base branch is `main`; the quality scale verification step and Core pylint plugin check replaced with this project's rules.

# Review Code Changes

## Scope:
- Unless instructed otherwise, review the full changes (the ones from the branch plus uncommitted ones) against the target branch. Resolve the base to an available ref (prefer `upstream/<base>`, then `origin/<base>`, then local `<base>`) and review `git diff "$(git merge-base "$BASE_REF" HEAD)"`; use `main` as the default base.

## Analyze the code changes for:
- Code quality and style consistency
- Potential bugs or issues
- Performance implications
- Security concerns
- Test coverage
- Documentation updates if needed
- Values that reach new code from existing code. Read the producing side, including its fallbacks and defaults, not only the diff.
- State added to an entity or coordinator. Check where it is reset, for example on reconnect or reload.
- Compliance with the Ontario 511 rules in `AGENTS.md`: API keys never exposed, every call goes through the shared rate limiter, attribution set on entities.
- The guidelines in the `ha-integration-knowledge` skill.

## Verification:
- After the review, run parallel subagents for each finding to double-check it.
- When a finding suggests a change, also check that the suggested change is valid, for example that it passes the project's ruff and strict mypy configuration.
- Spawn up to a maximum of 10 parallel subagents at a time.
- Gather the results from the subagents and summarize them in the final review comments.

## IMPORTANT:
- Just review. DO NOT make any changes.
- Be constructive and specific in your comments.
- Suggest improvements where appropriate.
- No need to run tests or linters, just review the code changes.
- No need to highlight things that are already good.

## Output format:
- List specific comments for each file/line that needs attention.
- In the end, summarize with an overall assessment (approve, request changes, or comment) and bullet point list of changes suggested, if any.
  - Example output:
    ```
    Overall assessment: request changes.
    1. [CRITICAL] sensor.py:143 - Memory leak
    2. [PROBLEM] data_processing.py:87 - Inefficient algorithm
    3. [SUGGESTION] test_init.py:45 - Improve x variable name
    4. [NIT][PRE-EXISTING] const.py:12 - Unused constant
    ```
  - Make sure to include the file and line number when possible in the bullet points.
  - Flag findings with `[PRE-EXISTING]` after the category when the issue already exists on the base branch and the change only touches, moves or sits next to that code. Confirm against the base branch before flagging. Pre-existing findings should not on their own make the overall assessment "request changes".
