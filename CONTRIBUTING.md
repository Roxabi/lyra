# Contributing

## Workflow

All development goes through the `staging` branch. `main` is the stable release branch.

```
feature/fix branch → PR → staging → (promote) → main
```

1. Create a branch from `staging` with a descriptive name: `feat/discord-voice`, `fix/pool-lock-timeout`
2. Open a PR targeting `staging`
3. Pass CI (lint, typecheck, tests)
4. Merge — auto-merge is enabled for approved PRs

## Commit conventions

[Conventional Commits](https://www.conventionalcommits.org/) — enforced by CI:

```
feat(hub): add wildcard binding support
fix(telegram): handle bot message filter edge case
chore: bump aiogram to 3.27
docs(adr): add ADR-008 phase 1 memory scope
test(hub): cover dispatch_response error path
refactor(pool): extract pool_id generation to RoutingKey
```

- Scope is optional but encouraged (`hub`, `telegram`, `discord`, `pool`, `agent`, `memory`)
- Breaking changes: add `!` after scope — `feat(hub)!: remove BindingKey`
- English only for all commits, code, and documentation

## PR conventions

- Title = the commit message that will land on `staging` (Conventional Commits format)
- Link the GitHub issue in the PR body: `Closes #42`
- Keep PRs focused — one logical change per PR
- All tests must pass before merge

## Code style

```bash
uv run ruff check .      # lint — must pass
uv run ruff format .     # format — auto-fix
uv run pyright           # type check — must pass
uv run pytest            # tests — must pass
```

Pre-commit hooks run `ruff check` and `ruff format` automatically on `git commit`. Install them once:

```bash
uv run pre-commit install
```

## Adding a channel adapter

A channel adapter normalizes messages from one platform into `InboundMessage` objects and sends `Response` objects back via the `OutboundDispatcher`.

**1. Add a `Platform` variant** in `src/lyra/core/message.py`:

```python
class Platform(str, Enum):
    TELEGRAM = "telegram"
    DISCORD  = "discord"
    SIGNAL   = "signal"        # new
```

**2. Create `src/lyra/adapters/signal.py`** implementing the `ChannelAdapter` protocol:

```python
class SignalAdapter:
    async def send(self, original_msg: InboundMessage, response: Response) -> None:
        ...
```

See `src/lyra/adapters/_shared.py` for shared normalization helpers and render functions (audio, attachments).

**4. Register it in `src/lyra/__main__.py`**:

```python
from lyra.adapters.signal import SignalAdapter

signal_adapter = SignalAdapter(hub=hub, bot_id="main")
hub.register_adapter(Platform.SIGNAL, "main", signal_adapter)
hub.register_binding(Platform.SIGNAL, "main", "*", "lyra", ...)
```

**5. Add tests** in `tests/adapters/test_signal.py` — mock the external SDK, test `_normalize()` and `send()`.

## Adding an agent

An agent is a stateless singleton defined by a TOML config. It processes messages via `agent.process(msg, pool) -> Response`.

**1. Create a TOML config** in `src/lyra/agents/my_agent.toml`:

```toml
[agent]
name = "my_agent"
memory_namespace = "my_agent"
permissions = []

[model]
backend = "claude-cli"
model = "claude-sonnet-4-5"
max_turns = 10
tools = ["Read", "Grep", "Glob"]

[prompt]
system = """You are ..."""
```

**2. Load and register in `__main__.py`**:

```python
my_config = load_agent_config("my_agent")
my_agent = SimpleAgent(my_config, cli_pool)
hub.register_agent(my_agent)
```

**3. Add a binding** so the hub routes messages to it:

```python
hub.register_binding(Platform.TELEGRAM, "main", "tg:user:123456", "my_agent", pool_id)
```

For a custom agent class (beyond `SimpleAgent`), subclass `AgentBase` from `src/lyra/core/agent.py` and implement `process()`.

## Code review expectations

Reviews focus on correctness, clarity, and architectural consistency — not style (ruff handles that).

**For authors:**
- Keep PRs focused; one logical change per PR makes review fast
- Add context in the PR description for non-obvious decisions
- Respond to review comments within 2 business days

**For reviewers:**
- Use [Conventional Comments](https://conventionalcomments.org/) to signal intent (`suggestion:`, `nit:`, `issue:`)
- Distinguish blocking from non-blocking feedback — prefix optional suggestions with `nit:`
- Approve once all `issue:` comments are resolved; `nit:` items can merge at author's discretion

## Architecture decisions (ADRs)

Significant architectural choices — especially irreversible ones — are recorded as ADRs in `docs/architecture/adr/`.

When to write an ADR:
- You're choosing between two or more real alternatives
- The decision will be painful to reverse after code is written
- Future contributors need to understand why the current approach was chosen

Use the dev-core skill to create one:

```
/adr "title of the decision"
```

Or copy an existing ADR file and follow the structure: **Status → Context → Options Considered → Decision → Consequences**.

After creating the ADR file, add its slug to `docs/architecture/adr/meta.json`.

## Project structure

```
src/lyra/
  core/           — hub, pool, agent, message (no external I/O)
  adapters/       — one file per channel (Telegram, Discord, ...)
  agents/         — agent implementations + TOML configs
tests/
  core/           — unit tests for core primitives
  adapters/       — adapter tests (mock external SDKs)
docs/
  architecture/
    adr/          — one .mdx per decision
artifacts/        — dev-core outputs (frames, plans, specs, analyses)
```

The `core/` layer has no imports from `adapters/` or `agents/`. Dependency direction: `adapters → core`, `agents → core`.
