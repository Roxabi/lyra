"""Command configuration dataclasses and defaults.

Extracted from command_router.py for maintainability.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.messaging.message import Response


@dataclass(frozen=True)
class CommandConfig:
    """Configuration for a built-in slash command."""

    description: str = ""
    builtin: bool = False
    timeout: float = 30.0


@dataclass(frozen=True)
class SessionCommandEntry:
    """Registry entry for a session command handler."""

    handler: Callable[..., Awaitable["Response"]]
    tools: object  # SessionTools
    description: str = ""
    timeout: float = 30.0


DEFAULT_BUILTINS: dict[str, CommandConfig] = {
    f"/{name}": CommandConfig(builtin=True, description=desc)
    for name, desc in [
        ("help", "List available commands"),
        ("circuit", "Show circuit breaker status (admin-only)"),
        ("routing", "Show smart routing decisions (admin-only)"),
        ("stop", "Cancel the current processing turn"),
        ("config", "Show/set runtime config (admin-only)"),
        ("clear", "Clear conversation history"),
        ("new", "Start a new session (alias for /clear)"),
        ("folder", "Switch working directory: /folder ~/projects/foo"),
        ("cd", "Alias for /folder"),
        ("workspace", "Switch workspace: /workspace <name> [question] | ls"),
        ("voice", "Enable voice mode (TTS replies) for this session"),
        ("text", "Disable voice mode (text-only replies)"),
    ]
}
