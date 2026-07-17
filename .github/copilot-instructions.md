# AI Agent Instructions

## Communication & Approach

- **Ask first, assume later.** The agent should ask for context or code examples if needed to understand the user's intent. Clarify together before proceeding.
- **Explain over examples.** When suggesting different solutions, the agent should explain the trade-offs and reasoning without immediately jumping to code examples.
- **Discuss design decisions.** If the user asks something that doesn't align with best practices, the agent should say so and explain why.

## Code Quality & Architecture

- **Single source of truth.** Avoid duplicating data or logic across multiple places. If a value is repeated and dependent (like a configuration value or constant), define it once and reference it everywhere.
- **Avoid hardcoding.** Use configuration files, environment variables, constants modules, or other appropriate mechanisms instead of hardcoding values directly in code.
- **Choose the right tool.** Recommend solutions based on what actually fits the problem, not what's easiest to implement.
- **Comment generosity.** Be somewhat comment-generous. Comments should describe intents and explain why some operations are done when they don't have a very obvious reason.

## Iteration & Collaboration

- **Explain before showing.** When iterating on changes together, the agent should first show what changed and *why*. This way the user understands the reasoning before reviewing the code.
- **Preserve existing comments.** When refactoring, try to keep existing comments about context. If they were written, it's likely they provide valuable context.
- **Check diagnostics.** When making changes, check for diagnostic errors and warnings to comply with linter demands and maintain code quality.
- **No change-tracking comments.** Don't add comments in code that highlight what was changed (e.g., `# CHANGED: updated logic here`). Comments should describe the current logic and intent, not compare to previous versions.

## Technical Preferences

- **Latest versions.** Use the latest versions of Python (3.14+) and libraries.
