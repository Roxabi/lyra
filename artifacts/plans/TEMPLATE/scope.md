# Workpack Scope

> Replace this file with the actual scope for your workpack.
> Keep it short — one paragraph or a tight bullet list.
> The agent reads this once at the start of the run.

## Issue

- **GitHub issue:** #NNN — _title of the issue_
- **Branch:** `feat/NNN-slug`
- **Worktree:** `.claude/worktrees/NNN-slug/`

## Goal

_One sentence describing what this run achieves at a high level._

Example: "Add `LlmProvider.stream()` to the Protocol and implement it in both drivers so that
`SimpleAgent` can consume typed `LlmEvent` objects instead of raw strings."

## Deliverables

List what will exist when this run is done:

- [ ] _File or feature 1_
- [ ] _File or feature 2_
- [ ] All tests passing (`uv run pytest`)
- [ ] Lint clean (`uv run ruff check .`)
- [ ] Typecheck clean (`uv run pyright`)

## Out of scope

_Anything the agent must NOT touch, even if it looks related._

- _Example: do not refactor `pool_processor.py` beyond the S2 bridge_
- _Example: do not update existing ADRs_

## Task sequence

Tasks run sequentially in order. See `task-NNNN.md` files for details.

| Task | File | Summary |
|------|------|---------|
| 0001 | `task-0001.md` | _one-line summary_ |
| 0002 | `task-0002.md` | _one-line summary_ |
