---
name: ha-pr-comment-audit
description: Audits the review comment threads on a GitHub pull request for this Home Assistant custom integration, flagging unaddressed comments and requests for clarification. Use when checking whether PR feedback has been handled, either standalone or as part of a full PR review.
---

> Adapted from [`ha-pr-comment-audit` in home-assistant/core](https://github.com/home-assistant/core/tree/7c3a98ae6f4454d65500438b2fb503363fcf490a/.claude/skills/ha-pr-comment-audit), licensed under the [Apache License 2.0](../../../LICENSE). Modified for this project: description wording only.

# Check PR Review Comments

## Instructions:
- Resolve the PR context first. If a PR number is not given, use 'gh pr view' to identify the current branch's PR.
- Fetch the review comment threads for the PR (e.g. 'gh api' for review threads/comments).
- Flag comments that have not been addressed. If the author has replied but has not implemented the suggestion, still flag it and summarize the reply.
- Flag comments for which the author has asked for clarification.
- Generate a summary of the flagged comments, including a link for each comment. Don't include comments that have been addressed.


## IMPORTANT:
- Only provide feedback in the CONSOLE. DO NOT ACT ON GITHUB.
