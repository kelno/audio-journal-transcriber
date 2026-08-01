# AI Agent Instructions

## Communication & Approach

- Ask for missing context before making assumptions. Clarify intent, constraints, and existing patterns when needed.
- Explain reasoning and trade-offs before proposing solutions or code.
- Challenge approaches that conflict with good practices and explain alternatives.

## Code Quality & Architecture

- Keep a single source of truth. Avoid duplicated logic, constants, or configuration values.
- Avoid hardcoded values. Prefer configuration, environment variables, constants, or appropriate abstractions.
- Add meaningful comments that explain intent, non-obvious decisions, or constraints. Avoid comments describing changes.

## Iteration & Collaboration

- Before modifying code, summarize what will change and why.
- Preserve and improve existing contextual comments when refactoring.
- Validate changes against IDE diagnostics, linters, and warnings. Use the diagnostic tool if available provided by the IDE as it alreadys runs the linter for you.
- For non-trivial changes, design the interface and tests before implementation. Use mocks or stubs to validate behavior and isolate dependencies.
- Always try to split the work into small slices of reviewable changes, and assume user will always want to review changes before any commit.
- Suggest commit names when you think we're ready to commit.

## Technical Preferences

- Prefer readable, maintainable code over clever or overly compact solutions.

## Project specifics

- Target Python 3.14+ and use modern language features and standard library APIs where appropriate.
- We use "uv" instead of directly pip. For example use "uv run pytest" instead of trying to activate the venv using other scripts.
- Check linter issues with both `uv run basedpyright <file>` and `uv run ruff check --fix <file>`
- When generating examples, don't assume workspace environment but personal usage.
- If you encounter error "tool input was not fully received", a common cause is that you didn't "cd" properly.
