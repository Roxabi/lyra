# Contributing

<!-- TODO: write this doc when there is more than one contributor -->

## Setup

```bash
git clone https://github.com/Roxabi/lyra.git
cd lyra
uv venv .venv
uv pip install -e ".[dev]"
.venv/bin/pytest
```

<!-- TODO: document:
  - branch naming: feat/<issue>-<slug>, fix/<issue>-<slug>
  - commit convention: Conventional Commits (feat:, fix:, chore:, docs:…)
  - PR flow: worktree → feat branch → PR → review → merge to main
  - quality gate: pytest + pyright before PR
-->

## Adding an adapter

<!-- TODO: link to docs/adapters.md once complete -->

## Adding an agent

<!-- TODO: link to docs/agents.md once complete -->

## Adding a skill

<!-- TODO: link to docs/skills.md once complete -->
