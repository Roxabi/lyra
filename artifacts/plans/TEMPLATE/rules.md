# Agent Behavior Rules

These rules apply for the duration of this workpack run.
The agent must follow them without deviation.

## Autonomy

- **Make all decisions independently.** Do not ask the user for input, clarification, or approval.
- **Choose the simpler, more conservative option** when two valid approaches exist.
- **Never pause mid-run** to report progress or seek confirmation.

## Error handling

- **Do not stop on non-blocking errors.** Log the issue as a comment in the affected file or in a
  `NOTES.md` at the root of the workpack directory, then proceed to the next task.
- **Stop only on:** a file that cannot be located, a dependency that cannot be resolved, or a
  quality gate that fails after 3 fix attempts.
- On a hard stop: write a `BLOCKED.md` at the root of the workpack directory describing the
  blocker, the task ID, and the last error message. Then exit cleanly.

## Quality

- Run `uv run ruff check . && uv run ruff format .` after every file edit.
- Run `uv run pytest` after each task that modifies source code (not docs-only tasks).
- Fix all lint and test failures before moving to the next task (max 3 attempts per failure).

## Scope

- Only touch files listed in the individual task files.
- Do not refactor, rename, or improve anything outside the stated task scope.
- Do not create new files unless a task explicitly requires it.

## Commits

- Commit after each task using Conventional Commits format.
- Message format: `<type>(<scope>): <description>` (e.g., `feat(hub): add wildcard binding`).
- Never amend commits. Never use `--force`. Never skip pre-commit hooks.
- Stage only the specific files changed by the task (never `git add -A` or `git add .`).
