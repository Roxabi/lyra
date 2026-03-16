"""Command router — dispatch slash commands to builtins, session handlers, or plugins.

Builtin implementations live in builtin_commands.py and workspace_commands.py (#298).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from . import builtin_commands, workspace_commands
from .command_parser import CommandContext
from .message import InboundMessage, Response
from .plugin_loader import AsyncHandler, PluginLoader
from .pool import Pool

if TYPE_CHECKING:
    from pathlib import Path

    from lyra.core.runtime_config import RuntimeConfigHolder
    from lyra.llm.base import LlmProvider
    from lyra.llm.smart_routing import SmartRoutingDecorator

    from .circuit_breaker import CircuitRegistry
    from .messages import MessageManager

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandConfig:
    """Configuration for a built-in slash command."""

    description: str = ""
    builtin: bool = False
    timeout: float = 30.0


@runtime_checkable
class SessionCommandHandler(Protocol):
    """Async handler: (msg, driver, args, timeout) -> Response. No pool history."""

    async def __call__(
        self,
        msg: InboundMessage,
        driver: "LlmProvider",
        args: list[str],
        timeout: float,
    ) -> Response: ...


@dataclass(frozen=True)
class SessionCommandEntry:
    """Registry entry for a session command (async, isolated LLM call)."""

    handler: SessionCommandHandler
    description: str = ""
    timeout: float = 60.0


class CommandRouter:
    """Routes slash commands to plugin handlers or built-in handlers."""

    _BARE_URL_RE: re.Pattern[str] = re.compile(r"^https?://\S+$")

    _DEFAULT_BUILTINS: dict[str, CommandConfig] = {
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
        ]
    }

    def __init__(  # noqa: PLR0913 — DI constructor, each arg is a required dependency
        self,
        plugin_loader: PluginLoader,
        enabled_plugins: list[str],
        builtins: dict[str, CommandConfig] | None = None,
        circuit_registry: CircuitRegistry | None = None,
        admin_user_ids: set[str] | None = None,
        msg_manager: MessageManager | None = None,
        runtime_config_holder: RuntimeConfigHolder | None = None,
        runtime_config_path: Path | None = None,
        smart_routing_decorator: SmartRoutingDecorator | None = None,
        on_debounce_change: Callable[[int], None] | None = None,
        workspaces: dict[str, Path] | None = None,
        session_driver: "LlmProvider | None" = None,
    ) -> None:
        self._plugin_loader = plugin_loader
        self._enabled_plugins = enabled_plugins
        self._builtins: dict[str, CommandConfig] = (
            builtins if builtins is not None else dict(self._DEFAULT_BUILTINS)
        )
        self._circuit_registry = circuit_registry
        self._admin_user_ids = admin_user_ids or set()
        self._msg_manager = msg_manager
        self._runtime_config_holder = runtime_config_holder
        self._runtime_config_path = runtime_config_path
        self._smart_routing = smart_routing_decorator
        self._on_debounce_change = on_debounce_change
        self._workspaces: dict[str, Path] = workspaces or {}
        self._session_driver: LlmProvider | None = session_driver
        self._session_handlers: dict[str, SessionCommandEntry] = {}
        self._passthroughs: set[str] = set()
        # Guard: raise early if any loaded plugin command clashes with a builtin.
        plugin_handlers = plugin_loader.get_commands(enabled_plugins)
        conflicts = set(plugin_handlers) & set(self._builtins)
        if conflicts:
            raise ValueError(
                f"Plugin command(s) clash with builtins: {sorted(conflicts)}. "
                "Rename the plugin command or remove the builtin."
            )

    def _rewrite_bare_url(self, msg: InboundMessage) -> InboundMessage:
        """Attach a synthetic /add CommandContext for a bare URL message."""
        url = msg.text.strip()
        ctx = CommandContext(prefix="/", name="add", args=url, raw=msg.text)
        return replace(msg, command=ctx)

    def prepare(self, msg: InboundMessage) -> InboundMessage:
        """Rewrite bare URL messages to /add; return the (possibly new) message."""
        if msg.command is None and self._BARE_URL_RE.fullmatch(msg.text.strip()):
            return self._rewrite_bare_url(msg)
        return msg

    def is_command(self, msg: InboundMessage) -> bool:
        """Return True if msg carries a CommandContext or is a bare URL."""
        if msg.command is not None:
            return True
        return bool(self._BARE_URL_RE.fullmatch(msg.text.strip()))

    def get_command_name(self, msg: InboundMessage) -> str | None:
        """Return full command name (e.g. '/join') or None. Single source of truth."""
        if msg.command is None:
            return None
        return f"{msg.command.prefix}{msg.command.name}"

    def register_passthrough(self, name: str) -> None:
        """Mark a command as agent-handled — dispatch() returns None."""
        self._passthroughs.add(f"/{name}")

    def register_session_command(
        self,
        name: str,
        handler: SessionCommandHandler,
        description: str = "",
        timeout: float = 60.0,
    ) -> None:
        """Register session command; name has no leading slash. Raises on clash."""
        cmd = f"/{name}"
        if cmd in self._builtins:
            raise ValueError(f"Session command {cmd!r} clashes with a builtin.")
        plugin_handlers = self._plugin_loader.get_commands(self._enabled_plugins)
        if cmd in plugin_handlers:
            raise ValueError(f"Session command {cmd!r} clashes with a plugin.")
        self._session_handlers[cmd] = SessionCommandEntry(
            handler=handler,
            description=description,
            timeout=timeout,
        )

    def _dispatch_builtin(
        self, command_name: str, args: list[str], msg: InboundMessage, pool: Pool | None
    ) -> Response | None:
        if command_name == "/help":
            return builtin_commands.help_command(
                self._builtins,
                self._session_handlers,
                self._plugin_loader,
                self._enabled_plugins,
                self._msg_manager,
            )
        if command_name == "/circuit":
            return builtin_commands.circuit_status(
                msg, self._admin_user_ids, self._circuit_registry
            )
        if command_name == "/routing":
            return builtin_commands.routing_status(
                msg, self._admin_user_ids, self._smart_routing
            )
        if command_name == "/stop":
            if pool is not None:
                pool.cancel()
            return Response(content="")
        if command_name == "/config":
            return builtin_commands.config_command(
                msg,
                args,
                self._admin_user_ids,
                self._runtime_config_holder,
                self._runtime_config_path,
                self._on_debounce_change,
            )
        builtin = self._builtins.get(command_name)
        if builtin and builtin.builtin:
            return Response(
                content=f"Built-in command {command_name} is not yet implemented."
            )
        return None

    async def _dispatch_session(
        self,
        name: str,
        args: list[str],
        msg: InboundMessage,
        pool: Pool | None,  # noqa: ARG002 — future use; session commands are pool-agnostic
    ) -> Response:
        """Dispatch a session command with timeout and driver checks."""
        entry = self._session_handlers[name]
        if self._session_driver is None:
            return Response(content="Session commands require anthropic-sdk backend.")
        try:
            return await asyncio.wait_for(
                entry.handler(msg, self._session_driver, args, entry.timeout),
                timeout=entry.timeout,
            )
        except TimeoutError:
            log.warning("Session command %s timed out after %.1fs", name, entry.timeout)
            return Response(content=f"Command timed out after {entry.timeout:.0f}s.")

    async def dispatch(  # noqa: C901 — command routing has inherent branching complexity
        self, msg: InboundMessage, pool: Pool | None = None
    ) -> Response | None:
        """Route pre-parsed command. Returns None for !-prefixed unknowns."""
        msg = self.prepare(msg)
        if msg.command is None:
            raise ValueError("dispatch() called on non-command message")

        command_name = f"{msg.command.prefix}{msg.command.name}"
        args = msg.command.args.split() if msg.command.args else []

        if command_name in ("/clear", "/new"):
            return await workspace_commands.cmd_clear(pool)
        if command_name in ("/folder", "/cd"):
            return await workspace_commands.cmd_folder(
                msg, args, pool, self._admin_user_ids
            )
        if command_name == "/workspace":
            return await workspace_commands.cmd_workspace(
                msg, args, pool, self._admin_user_ids, self._workspaces
            )

        builtin_response = self._dispatch_builtin(command_name, args, msg, pool)
        if builtin_response is not None:
            return builtin_response

        if command_name in self._session_handlers:
            return await self._dispatch_session(command_name, args, msg, pool)

        plugin_handlers: dict[str, AsyncHandler] = self._plugin_loader.get_commands(
            self._enabled_plugins
        )
        handler = plugin_handlers.get(command_name)
        if handler is None:
            if msg.command.prefix == "!" or command_name in self._passthroughs:
                return None
            _fallback = (
                f"Unknown command: {command_name}. "
                "Type /help for available commands."
            )
            _reply_text = (
                self._msg_manager.get("unknown_command", command_name=command_name)
                if self._msg_manager
                else _fallback
            )
            return Response(content=_reply_text)

        if pool is None:
            raise TypeError(
                f"Plugin command '{command_name}' requires a Pool instance."
            )
        timeout = self._plugin_loader.get_timeout(command_name, self._enabled_plugins)
        try:
            return await asyncio.wait_for(handler(msg, pool, args), timeout=timeout)
        except TimeoutError:
            log.warning(
                "Plugin command %s timed out after %.1fs",
                command_name, timeout,
            )
            return Response(
                content=f"Command {command_name} timed out "
                f"after {timeout:.0f}s."
            )
