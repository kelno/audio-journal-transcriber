# AI Agent Instructions

## Working on this repository

- Validate changes against IDE diagnostics, linters, and warnings. Use the diagnostic tool the IDE provides, if available, as it already runs the linter for you.
- Always try to split the work into small slices of reviewable changes.
- Commit freely on a feature branch or worktree, in small reviewable commits. Never commit on main: the user reviews a branch before it is merged into main.

## Project specifics

- Target Python 3.14+ and use modern language features and standard library APIs where appropriate.
- Run the tests with `uv run pytest`.
- Check linter issues with both `uv run basedpyright <file>` and `uv run ruff check --fix <file>`.
- When generating examples, don't assume workspace environment but personal usage.
- If you encounter error "tool input was not fully received", a common cause is that you didn't "cd" properly.
