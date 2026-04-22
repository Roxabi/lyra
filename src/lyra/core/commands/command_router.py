"""Command router — dispatch slash commands to builtins, processors, or plugins."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import RouterConfig
from ..messaging.message import InboundMessage, Response
from ..pool.pool import Pool
from . import builtin_commands, workspace_commands
from .command_config import DEFAULT_BUILTINS, CommandConfig, SessionCommandEntry
from .command_loader import AsyncHandler, CommandLoader
from .command_parser import CommandContext
from .command_patterns import (
    check_command_conflicts,
    format_timeout_message,
    format_unknown_command,
    is_admin_only,
    is_bare_url,
    rewrite_bare_url,
)

if TYPE_CHECKING:
    from lyra.core.runtime_config import RuntimeConfigHolder
    from lyra.core.smart_routing_protocol import SmartRoutingProtocol

    from ..circuit_breaker import CircuitRegistry
    from ..messaging.messages import MessageManager

log = logging.getLogger(__name__)

BuiltinHandler = Callable[
    [list, InboundMessage, Pool | None], Response | Awaitable[Response] | None
]  # noqa: E501


class CommandRouter:
    """Routes slash commands to plugin handlers or built-in handlers."""

    def __init__(  # noqa: PLR0913
        self,
        command_loader: CommandLoader,
        enabled_plugins: list[str],
        circuit_registry: "CircuitRegistry | None" = None,
        msg_manager: "MessageManager | None" = None,
        runtime_config_holder: "RuntimeConfigHolder | None" = None,
        runtime_config_path: Path | None = None,
        smart_routing_decorator: "SmartRoutingProtocol | None" = None,
        config: RouterConfig | None = None,
        # Backward-compat: individual params override config (deprecated)
        builtins: dict[str, CommandConfig] | None = None,
        on_debounce_change: Callable[[int], None] | None = None,
        on_cancel_change: Callable[[bool], None] | None = None,
        workspaces: dict[str, Path] | None = None,
        patterns: dict[str, bool] | None = None,
        pattern_configs: dict[str, dict] | None = None,
        session_driver: object = None,
    ) -> None:
        cfg: RouterConfig = config if config is not None else RouterConfig()
        # Allow individual param overrides for backward compat
        self._command_loader = command_loader
        self._enabled_plugins = enabled_plugins
        default_builtins = cfg.builtins or dict(DEFAULT_BUILTINS)
        self._builtins: dict[str, CommandConfig] = (
            builtins if builtins is not None else default_builtins
        )
        self._circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._runtime_config_holder = runtime_config_holder
        self._runtime_config_path = runtime_config_path
        self._smart_routing = smart_routing_decorator
        self._on_debounce_change = on_debounce_change or cfg.on_debounce_change
        self._on_cancel_change = on_cancel_change or cfg.on_cancel_change
        self._workspaces: dict[str, Path] = workspaces or {}
        self._patterns: dict[str, bool] = patterns or cfg.patterns
        self._pattern_configs: dict[str, dict] = (
            pattern_configs if pattern_configs is not None else cfg.pattern_configs
        )
        self._passthroughs: set[str] = set()
        self._session_driver: object = session_driver or cfg.session_driver
        self._session_handlers: dict[str, SessionCommandEntry] = {}
        self._builtin_handlers = self._build_builtin_handlers()
        check_command_conflicts(
            command_loader.get_commands(enabled_plugins), self._builtins
        )

    @classmethod
    def builtin_metadata(cls) -> list[tuple[str, str, bool]]:
        """Return (name, description, admin_only) for default builtins only."""
        return [
            (name, cfg.description, is_admin_only(cfg.description))
            for name, cfg in DEFAULT_BUILTINS.items()
        ]

    def command_metadata(self) -> list[tuple[str, str, bool]]:
        """Return (name, description, admin_only) for all registered commands."""
        result: list[tuple[str, str, bool]] = []
        for name, cfg in self._builtins.items():
            result.append((name, cfg.description, is_admin_only(cfg.description)))
        plugin_descs = self._command_loader.get_command_descriptions(
            self._enabled_plugins
        )
        for name, desc in plugin_descs.items():
            result.append((name, desc, is_admin_only(desc)))
        # Processor commands — only include those registered as passthroughs.
        try:
            importlib.import_module("lyra.core.processors")
            from lyra.core.processors.processor_registry import (
                registry as _proc_registry,
            )

            for cmd, desc in _proc_registry.descriptions().items():
                if cmd in self._passthroughs:
                    result.append((cmd, desc, False))
        except Exception as exc:
            log.debug("Could not load processor commands: %s", exc)
        return sorted(result)

    def prepare(self, msg: InboundMessage) -> InboundMessage:
        """Rewrite bare URLs per patterns.toml when patterns.bare_url is True."""
        if msg.command is None and is_bare_url(msg.text, self._patterns):
            return rewrite_bare_url(msg, self._pattern_configs, CommandContext)
        return msg

    def is_command(self, msg: InboundMessage) -> bool:
        """Return True if msg carries a CommandContext or is a rewritable bare URL."""
        return msg.command is not None or is_bare_url(msg.text, self._patterns)

    def get_command_name(self, msg: InboundMessage) -> str | None:
        if msg.command is None:
            return None
        return f"{msg.command.prefix}{msg.command.name}"

    def register_passthrough(self, name: str) -> None:
        self._passthroughs.add(f"/{name}")

    def register_session_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[Response]],
        *,
        tools: object,
        description: str = "",
        timeout: float = 30.0,
    ) -> None:
        """Register a session command handler.

        Deprecated: prefer ``@register`` from ``lyra.core.processors.processor_registry``.
        *name* must not include the leading slash. Raises ``ValueError`` on clash.
        """  # noqa: E501
        full_name = f"/{name}"
        if full_name in self._builtins:
            raise ValueError(f"Session command {full_name!r} clashes with a builtin.")
        plugin_handlers = self._command_loader.get_commands(self._enabled_plugins)
        if full_name in plugin_handlers:
            raise ValueError(f"Session command {full_name!r} clashes with a plugin.")
        self._session_handlers[full_name] = SessionCommandEntry(
            handler=handler, tools=tools, description=description, timeout=timeout
        )

    def _build_builtin_handlers(self) -> dict[str, BuiltinHandler]:
        """Build dispatch table for builtin commands."""

        def _stop(_a: list, _m: InboundMessage, p: Pool | None) -> Response:
            if p:
                p.cancel()
            return Response(content="")

        def _voice(a: list, _m: InboundMessage, p: Pool | None) -> Response | None:
            if a:
                return None  # passthrough
            if p:
                p.voice_mode = True
            return Response(content="Voice mode on — replies will be spoken.")

        def _text(_a: list, _m: InboundMessage, p: Pool | None) -> Response:
            if p:
                p.voice_mode = False
            return Response(content="Voice mode off — text-only replies.")

        return {
            "/help": lambda a, m, p: builtin_commands.help_command(
                self._builtins,
                self._session_handlers,
                self._command_loader,
                self._enabled_plugins,
                self._msg_manager,
                frozenset(self._passthroughs),
            ),
            "/circuit": lambda a, m, p: builtin_commands.circuit_status(
                m, self._circuit_registry
            ),
            "/routing": lambda a, m, p: builtin_commands.routing_status(
                m, self._smart_routing
            ),
            "/stop": _stop,
            "/config": lambda a, m, p: builtin_commands.config_command(
                m,
                a,
                self._runtime_config_holder,
                self._runtime_config_path,
                self._on_debounce_change,
                self._on_cancel_change,
            ),
            "/voice": _voice,
            "/text": _text,
            "/clear": lambda a, m, p: workspace_commands.cmd_clear(p),
            "/new": lambda a, m, p: workspace_commands.cmd_clear(p),
            "/folder": lambda a, m, p: workspace_commands.cmd_folder(m, a, p),
            "/cd": lambda a, m, p: workspace_commands.cmd_folder(m, a, p),
            "/workspace": lambda a, m, p: workspace_commands.cmd_workspace(
                m, a, p, self._workspaces
            ),
        }

    async def _dispatch_builtin(
        self, command_name: str, args: list[str], msg: InboundMessage, pool: Pool | None
    ) -> Response | None:
        handlers = self._builtin_handlers
        handler = handlers.get(command_name)
        if handler is not None:
            result = handler(args, msg, pool)
            if inspect.isawaitable(result):
                return await result
            return result
        builtin = self._builtins.get(command_name)
        if builtin and builtin.builtin:
            return Response(
                content=f"Built-in command {command_name} is not yet implemented."
            )
        return None

    async def dispatch(  # noqa: C901
        self, msg: InboundMessage, pool: Pool | None = None
    ) -> Response | None:
        msg = self.prepare(msg)
        if msg.command is None:
            raise ValueError("dispatch() called on non-command message")
        command_name = f"{msg.command.prefix}{msg.command.name}"
        args = msg.command.args.split() if msg.command.args else []
        builtin_response = await self._dispatch_builtin(command_name, args, msg, pool)
        if builtin_response is not None:
            return builtin_response
        session_entry = self._session_handlers.get(command_name)
        if session_entry is not None:
            if self._session_driver is None:
                return Response(
                    content="Session commands require an anthropic-sdk backend. "
                    "No session driver is configured."
                )
            try:
                return await asyncio.wait_for(
                    session_entry.handler(
                        msg,
                        self._session_driver,
                        session_entry.tools,
                        args,
                        session_entry.timeout,
                    ),
                    timeout=session_entry.timeout,
                )
            except asyncio.TimeoutError:
                log.warning(
                    "Session command %s timed out after %.1fs",
                    command_name,
                    session_entry.timeout,
                )
                return Response(
                    content=format_timeout_message(command_name, session_entry.timeout)
                )
        plugin_handlers: dict[str, AsyncHandler] = self._command_loader.get_commands(
            self._enabled_plugins
        )
        handler = plugin_handlers.get(command_name)
        if handler is None:
            if msg.command.prefix == "!" or command_name in self._passthroughs:
                return None
            reply = (
                self._msg_manager.get("unknown_command", command_name=command_name)
                if self._msg_manager
                else format_unknown_command(command_name)
            )
            return Response(content=reply)
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
            return Response(content=format_timeout_message(command_name, timeout))
