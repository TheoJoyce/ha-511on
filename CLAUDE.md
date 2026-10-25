The project instructions for this repository live in AGENTS.md. Read it before doing any work here.

@AGENTS.md

## Claude Code specifics

- Use the project skills in `.claude/skills/`: `ha-integration-knowledge` when building or changing the integration, `ha-review` for code reviews, and `ha-pr-reviewer` for GitHub pull requests.
- Never read, print or echo `.env`; `.claude/settings.json` denies reading it. Code that needs the key (such as `scripts.record_fixtures`) reads it itself.
- Pushing workflow files needs a GitHub token with the `workflow` scope. If a push is rejected for that reason, ask the user to run `gh auth refresh -h github.com -s workflow`.
- Force-pushing, or amending a commit that has been pushed, needs the user's explicit approval each time.
