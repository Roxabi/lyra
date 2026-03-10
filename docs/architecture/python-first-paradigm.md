# Python-First CLI/Library Paradigm

Lyra follows a **library-first** architecture where the CLI is a thin orchestration layer and all business logic lives in importable library code.

## Core Principle

```
__main__.py (orchestration)  -->  core/ + adapters/ + agents/ (library)
```

The entry point (`python -m lyra`) does only three things:
1. Load configuration (env vars + TOML)
2. Wire instances together (Hub, Adapters, Agents)
3. Call `hub.run()`

No business logic in `__main__.py`. Ever.

## Package Structure

```
src/lyra/
  __init__.py            # Namespace only
  __main__.py            # Thin orchestration
  config.py              # Re-exports adapter configs
  core/                  # Domain logic
    __init__.py          # Public API via __all__
    agent.py             # AgentBase ABC + Agent config
    hub.py               # Hub (bus + registry + bindings)
    pool.py              # Pool (history + lock)
    message.py           # Message, Platform, Response types
    cli_pool.py          # Subprocess pool for Claude CLI
    command_router.py    # Slash command routing
  adapters/              # Channel adapters (Telegram, Discord)
    __init__.py          # Public API via __all__
    telegram.py
    discord.py
  agents/                # Agent implementations
    __init__.py          # Public API via __all__
    simple_agent.py      # Wraps CliPool
    anthropic_agent.py   # Direct SDK calls
    lyra_default.toml    # Default agent config
```

## Design Patterns

### 1. Protocol-based extensibility

Adapters implement `ChannelAdapter` Protocol (not ABC):
```python
class ChannelAdapter(Protocol):
    async def send(self, original_msg: Message, response: Response) -> None: ...
    async def send_streaming(self, original_msg: Message, chunks: AsyncIterator[str]) -> None: ...
```

Agents subclass `AgentBase` ABC:
```python
class AgentBase(ABC):
    @abstractmethod
    async def process(self, msg: Message, pool: Pool) -> Response: ...
```

### 2. Three-layer configuration

| Layer | Purpose | Format |
|-------|---------|--------|
| Environment variables | Secrets (tokens, API keys) | `.env` |
| TOML agent configs | Behavior, model, tools | `agents/*.toml` |
| Vault personas | Identity, personality | `~/.roxabi-vault/personas/*.toml` |

### 3. asyncio-first concurrency

- Single event loop, per-user `asyncio.Lock` in Pool
- Cross-user parallelism is automatic
- Bounded queue (100 messages) for backpressure
- CliPool: persistent subprocesses to avoid startup overhead

### 4. Public API via `__all__`

Each package exposes its public API explicitly:
```python
# core/__init__.py
__all__ = ["Agent", "AgentBase", "Hub", "Message", "Pool", "Response", ...]
```

Internal implementation details stay private. External code imports from package level:
```python
from lyra.core import Hub, Agent, Message
from lyra.adapters import TelegramAdapter
```

## Factory Pattern

Agent creation uses backend-based dispatch:
```python
def _create_agent(config: Agent, cli_pool: CliPool | None) -> AgentBase:
    if config.model_config.backend == "anthropic-sdk":
        return AnthropicAgent(config)
    if config.model_config.backend in ("claude-cli", "ollama"):
        return SimpleAgent(config, cli_pool)
```

## Testing Philosophy

- **Test the library, not the CLI**: Import and test `core/`, `adapters/`, `agents/` directly
- **Mock adapters**: Use `FakeAdapter` implementing the Protocol
- **Monkeypatch `__main__`**: For integration tests only
- **No CLI framework to test**: `__main__.py` is so thin it barely needs testing

## When to Apply This Paradigm

- **Daemon/service projects** (Lyra, 2ndBrain): `python -m <pkg>` entry point, no CLI framework
- **CLI tool projects** (voiceCLI, imageCLI): Use Click/Typer for subcommands, but keep business logic in library modules
- **Both**: Library code is always importable and testable without the CLI layer

## Build System

- `pyproject.toml` with `hatchling` backend
- `requires-python = ">=3.12"`
- `src/` layout: `[tool.hatch.build.targets.wheel] packages = ["src/lyra"]`
- Dependencies managed via `uv` (lockfile: `uv.lock`)
