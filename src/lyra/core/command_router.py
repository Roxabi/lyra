"""Command router — dispatch slash commands to builtins, processor commands, or plugins.

Builtin implementations live in builtin_commands.py and workspace_commands.py (#298).
Processor commands (pre/post hooks into the pool flow) live in processor_registry.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from . import builtin_commands, workspace_commands
from .command_loader import AsyncHandler, CommandLoader
from .command_parser import CommandContext
from .message import InboundMessage, Response
from .pool import Pool

if TYPE_CHECKING:
    from lyra.core.runtime_config import RuntimeConfigHolder
    from lyra.llm.smart_routing import SmartRoutingDecorator

    from .circuit_breaker import CircuitRegistry
    from .messages import MessageManager

_BUNDLED_PATTERNS_CONFIG = (
    Path(__file__).resolve().parent.parent / "config" / "patterns.toml"
)


def _load_pattern_configs(path: Path | None = None) -> dict[str, dict]:
    """Load pattern rule configs from TOML. Falls back to bundled defaults."""
    target = path or _BUNDLED_PATTERNS_CONFIG
    try:
        with open(target, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandConfig:
    """Configuration for a built-in slash command."""

    description: str = ""
    builtin: bool = False
    timeout: float = 30.0


@dataclass(frozen=True)
class SessionCommandEntry:
    """Registry entry for a session command handler."""

    handler: object  # AsyncHandler: (msg, driver, tools, args, timeout) -> Response
    tools: object  # SessionTools
    description: str = ""
    timeout: float = 30.0


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
        command_loader: CommandLoader,
        enabled_plugins: list[str],
        builtins: dict[str, CommandConfig] | None = None,
        circuit_registry: "CircuitRegistry | None" = None,
        msg_manager: "MessageManager | None" = None,
        runtime_config_holder: "RuntimeConfigHolder | None" = None,
        runtime_config_path: Path | None = None,
        smart_routing_decorator: "SmartRoutingDecorator | None" = None,
        on_debounce_change: Callable[[int], None] | None = None,
        workspaces: dict[str, Path] | None = None,
        patterns: dict[str, bool] | None = None,
        pattern_configs: dict[str, dict] | None = None,
        session_driver: object = None,
    ) -> None:
        self._command_loader = command_loader
        self._enabled_plugins = enabled_plugins
        self._builtins: dict[str, CommandConfig] = (
            builtins if builtins is not None else dict(self._DEFAULT_BUILTINS)
        )
        self._circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._runtime_config_holder = runtime_config_holder
        self._runtime_config_path = runtime_config_path
        self._smart_routing = smart_routing_decorator
        self._on_debounce_change = on_debounce_change
        self._workspaces: dict[str, Path] = workspaces or {}
        self._patterns: dict[str, bool] = patterns or {}
        self._pattern_configs: dict[str, dict] = (
            pattern_configs if pattern_configs is not None else _load_pattern_configs()
        )
        self._passthroughs: set[str] = set()
        self._session_driver: object = session_driver
        self._session_handlers: dict[str, SessionCommandEntry] = {}
        # Guard: raise early if any loaded plugin command clashes with a builtin.
        plugin_handlers = command_loader.get_commands(enabled_plugins)
        conflicts = set(plugin_handlers) & set(self._builtins)
        if conflicts:
            raise ValueError(
                f"Plugin command(s) clash with builtins: {sorted(conflicts)}. "
                "Rename the plugin command or remove the builtin."
            )

    @staticmethod
    def _is_admin_only(description: str) -> bool:
        """Check if a command description marks it as admin-only."""
        return "(admin-only)" in description.lower()

    @classmethod
    def builtin_metadata(cls) -> list[tuple[str, str, bool]]:
        """Return (name, description, admin_only) for default builtins only.

        Class method — usable without instantiation (e.g., from CLI).
        """
        return [
            (name, cfg.description, cls._is_admin_only(cfg.description))
            for name, cfg in cls._DEFAULT_BUILTINS.items()
        ]

    def command_metadata(self) -> list[tuple[str, str, bool]]:
        """Return (name, description, admin_only) for all registered commands.

        Includes builtins and plugin commands. Admin-only is derived from
        the description containing '(admin-only)'.
        """
        result: list[tuple[str, str, bool]] = []
        for name, cfg in self._builtins.items():
            result.append(
                (name, cfg.description, self._is_admin_only(cfg.description))
            )
        plugin_descs = self._command_loader.get_command_descriptions(
            self._enabled_plugins
        )
        for name, desc in plugin_descs.items():
            result.append((name, desc, self._is_admin_only(desc)))
        return sorted(result)

    def _rewrite_bare_url(self, msg: InboundMessage) -> InboundMessage:
        """Attach a synthetic command CommandContext for a bare URL message.

        Target command is read from patterns.toml [bare_url].command.
        """
        url = msg.text.strip()
        command = self._pattern_configs.get("bare_url", {}).get("command", "vault-add")
        ctx = CommandContext(prefix="/", name=command, args=url, raw=msg.text)
        return replace(msg, command=ctx)

    def prepare(self, msg: InboundMessage) -> InboundMessage:
        """Rewrite bare URLs per patterns.toml when patterns.bare_url is True."""
        if msg.command is None and self._BARE_URL_RE.fullmatch(msg.text.strip()):
            if self._patterns.get("bare_url", False):
                return self._rewrite_bare_url(msg)
        return msg

    def is_command(self, msg: InboundMessage) -> bool:
        """Return True if msg carries a CommandContext or is a rewritable bare URL."""
        if msg.command is not None:
            return True
        if self._patterns.get("bare_url", False):
            return bool(self._BARE_URL_RE.fullmatch(msg.text.strip()))
        return False

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
        handler: object,
        *,
        tools: object,
        description: str = "",
        timeout: float = 30.0,
    ) -> None:
        """Register a session command handler.

        *name* must not include the leading slash (e.g. ``"vault-add"``).
        Raises ``ValueError`` if the name clashes with a builtin or plugin command.
        """
        full_name = f"/{name}"
        if full_name in self._builtins:
            raise ValueError(
                f"Session command {full_name!r} clashes with a builtin. "
                "Use a different name."
            )
        plugin_handlers = self._command_loader.get_commands(self._enabled_plugins)
        if full_name in plugin_handlers:
            raise ValueError(
                f"Session command {full_name!r} clashes with a plugin. "
                "Use a different name."
            )
        self._session_handlers[full_name] = SessionCommandEntry(
            handler=handler,
            tools=tools,
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
                self._command_loader,
                self._enabled_plugins,
                self._msg_manager,
            )
        if command_name == "/circuit":
            return builtin_commands.circuit_status(msg, self._circuit_registry)
        if command_name == "/routing":
            return builtin_commands.routing_status(msg, self._smart_routing)
        if command_name == "/stop":
            if pool is not None:
                pool.cancel()
            return Response(content="")
        if command_name == "/config":
            return builtin_commands.config_command(
                msg,
                args,
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
            return await workspace_commands.cmd_folder(msg, args, pool)
        if command_name == "/workspace":
            return await workspace_commands.cmd_workspace(
                msg, args, pool, self._workspaces
            )

        builtin_response = self._dispatch_builtin(command_name, args, msg, pool)
        if builtin_response is not None:
            return builtin_response

        session_entry = self._session_handlers.get(command_name)
        if session_entry is not None:
            if self._session_driver is None:
                return Response(
                    content=(
                        "Session commands require an anthropic-sdk backend. "
                        "No session driver is configured."
                    )
                )
            import asyncio as _asyncio

            try:
                return await _asyncio.wait_for(
                    session_entry.handler(  # type: ignore[operator]
                        msg,
                        self._session_driver,
                        session_entry.tools,
                        args,
                        session_entry.timeout,
                    ),
                    timeout=session_entry.timeout,
                )
            except _asyncio.TimeoutError:
                log.warning(
                    "Session command %s timed out after %.1fs",
                    command_name,
                    session_entry.timeout,
                )
                return Response(
                    content=(
                        f"Command {command_name} timed out"
                        f" after {session_entry.timeout:.0f}s."
                    )
                )

        plugin_handlers: dict[str, AsyncHandler] = self._command_loader.get_commands(
            self._enabled_plugins
        )
        handler = plugin_handlers.get(command_name)
        if handler is None:
            if msg.command.prefix == "!" or command_name in self._passthroughs:
                return None
            _fallback = (
                f"Unknown command: {command_name}. Type /help for available commands."
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
        timeout = self._command_loader.get_timeout(command_name, self._enabled_plugins)
        try:
            return await asyncio.wait_for(handler(msg, pool, args), timeout=timeout)
        except TimeoutError:
            log.warning(
                "Plugin command %s timed out after %.1fs",
                command_name,
                timeout,
            )
            return Response(
                content=f"Command {command_name} timed out after {timeout:.0f}s."
            )
