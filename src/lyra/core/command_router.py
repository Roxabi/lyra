"""Command router for Lyra hub (issue #66, updated #106).

Dispatches slash-prefixed messages to plugin handlers loaded by PluginLoader,
or to built-in handlers (/help, /circuit). SkillHandler and SKILL_REGISTRY removed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from .circuit_breaker import CircuitRegistry
from .message import InboundMessage, Response
from .messages import MessageManager
from .plugin_loader import AsyncHandler, PluginLoader
from .pool import Pool

if TYPE_CHECKING:
    from lyra.core.runtime_config import RuntimeConfigHolder
    from lyra.llm.smart_routing import SmartRoutingDecorator

log = logging.getLogger(__name__)

# Matches a message that starts with "/" followed by at least one word character.
_COMMAND_RE = re.compile(r"^/\w")


@dataclass(frozen=True)
class CommandConfig:
    """Configuration for a built-in slash command."""

    description: str = ""
    builtin: bool = False
    timeout: float = 30.0


class CommandRouter:
    """Routes slash commands to plugin handlers or built-in handlers."""

    _DEFAULT_BUILTINS: dict[str, CommandConfig] = {
        "/help": CommandConfig(builtin=True, description="List available commands"),
        "/circuit": CommandConfig(
            builtin=True, description="Show circuit breaker status (admin-only)"
        ),
        "/routing": CommandConfig(
            builtin=True, description="Show smart routing decisions (admin-only)"
        ),
        "/stop": CommandConfig(
            builtin=True, description="Cancel the current processing turn"
        ),
        "/config": CommandConfig(
            builtin=True, description="Show/set runtime config (admin-only)"
        ),
        "/clear": CommandConfig(builtin=True, description="Clear conversation history"),
        "/new": CommandConfig(
            builtin=True, description="Start a new session (alias for /clear)"
        ),
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
        # Register workspace commands as builtins
        for ws_name in self._workspaces:
            cmd = f"/{ws_name}"
            if cmd not in self._builtins:
                self._builtins[cmd] = CommandConfig(
                    builtin=True,
                    description=f"Switch to workspace: {self._workspaces[ws_name]}",
                )
        # Guard: raise early if any loaded plugin command clashes with a builtin.
        plugin_handlers = plugin_loader.get_commands(enabled_plugins)
        conflicts = set(plugin_handlers) & set(self._builtins)
        if conflicts:
            raise ValueError(
                f"Plugin command(s) clash with builtins: {sorted(conflicts)}. "
                "Rename the plugin command or remove the builtin."
            )

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def is_command(self, msg: InboundMessage) -> bool:
        """Return True if the message starts with '/' followed by a word char."""
        return bool(_COMMAND_RE.match(msg.text))

    def get_command_name(self, msg: InboundMessage) -> str | None:
        """Extract the slash-command name (e.g. '/join') or None if not a command.

        Single source of truth for command-name parsing, used by the hub pairing
        gate and by dispatch().
        """
        if not _COMMAND_RE.match(msg.text):
            return None
        return msg.text.split(maxsplit=1)[0].lower()

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch_builtin(
        self, command_name: str, args: list[str], msg: InboundMessage, pool: Pool | None
    ) -> Response | None:
        if command_name == "/help":
            return self._help()
        if command_name == "/circuit":
            return self._circuit_status(msg)
        if command_name == "/routing":
            return self._routing_status(msg)
        if command_name == "/stop":
            if pool is not None:
                pool.cancel()
            return Response(content="")
        if command_name == "/config":
            return self._cmd_config(msg, args)
        builtin = self._builtins.get(command_name)
        if builtin and builtin.builtin:
            return Response(
                content=f"Built-in command {command_name} is not yet implemented."
            )
        return None

    async def dispatch(self, msg: InboundMessage, pool: Pool | None = None) -> Response:
        """Parse the command name + args and route to the appropriate handler."""
        parts = msg.text.split()
        command_name = parts[0].lower()
        args = parts[1:]

        # /clear and /new need async session reset — handle before _dispatch_builtin
        if command_name in ("/clear", "/new"):
            return await self._cmd_clear(pool)

        # Workspace switching — async because pool.switch_workspace() is async
        ws_key = command_name.lstrip("/")
        if ws_key in self._workspaces:
            cwd = self._workspaces[ws_key]
            if pool is None:
                return Response(content=f"Context: {cwd}")
            await pool.switch_workspace(cwd)
            remaining = " ".join(args).strip()
            if remaining:
                followup = replace(msg, text=remaining, text_raw=remaining)
                pool.submit(followup)
            return Response(content=f"Context: {cwd}")

        builtin_response = self._dispatch_builtin(command_name, args, msg, pool)
        if builtin_response is not None:
            return builtin_response

        plugin_handlers: dict[str, AsyncHandler] = self._plugin_loader.get_commands(
            self._enabled_plugins
        )
        handler = plugin_handlers.get(command_name)
        if handler is None:
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
                f"Plugin command '{command_name}' requires a real Pool instance. "
                "Pass pool=<Pool> when calling dispatch() for plugin commands."
            )
        timeout = self._plugin_loader.get_timeout(command_name, self._enabled_plugins)
        try:
            return await asyncio.wait_for(handler(msg, pool, args), timeout=timeout)
        except TimeoutError:
            log.warning(
                "Plugin command %s timed out after %.1fs", command_name, timeout
            )
            return Response(
                content=f"Command {command_name} timed out after {timeout:.0f}s."
            )

    # ------------------------------------------------------------------
    # Builtins
    # ------------------------------------------------------------------

    def _help(self) -> Response:
        """Return a listing of all available commands (builtins + plugins)."""
        header = (
            self._msg_manager.get("help_header")
            if self._msg_manager
            else "Available commands:"
        )
        lines: list[str] = [header]
        # Builtins
        for cmd_name, cfg in sorted(self._builtins.items()):
            desc = cfg.description or "(no description)"
            lines.append(f"  {cmd_name} — {desc}")
        # Plugin commands
        plugin_handlers = self._plugin_loader.get_commands(self._enabled_plugins)
        for cmd_name in sorted(plugin_handlers):
            if cmd_name not in self._builtins:
                lines.append(f"  {cmd_name} — (plugin command)")
        return Response(content="\n".join(lines))

    def _circuit_status(self, msg: InboundMessage) -> Response:
        """Return circuit status table (admin-only)."""
        sender_id = msg.user_id
        if not self._admin_user_ids or sender_id not in self._admin_user_ids:
            return Response(content="This command is admin-only.")
        if self._circuit_registry is None:
            return Response(content="Circuit breaker not configured.")
        all_status = self._circuit_registry.get_all_status()
        lines = ["Circuit Status", "─" * 38]
        for name, status in sorted(all_status.items()):
            if status.retry_after is not None:
                state_str = f"OPEN       retry in {int(status.retry_after)}s"
            else:
                state_str = f"{status.state.value.upper():<10} (ok)"
            lines.append(f"  {name:<12} {state_str}")
        lines.append("─" * 38)
        return Response(content="\n".join(lines))

    def _routing_status(self, msg: InboundMessage) -> Response:
        """Return smart routing decisions table (admin-only)."""
        if not self._admin_user_ids or msg.user_id not in self._admin_user_ids:
            return Response(content="This command is admin-only.")
        if self._smart_routing is None:
            return Response(content="Smart routing not configured.")
        history = self._smart_routing.history
        if not history:
            return Response(content="No routing decisions yet.")
        lines = ["Smart Routing — Recent Decisions", "─" * 60]
        for decision in reversed(history):
            from datetime import datetime, timezone

            ts = datetime.fromtimestamp(decision.timestamp, tz=timezone.utc)
            ts_str = ts.strftime("%H:%M:%S")
            lines.append(
                f"  {ts_str}  {decision.complexity.value:<8}  "
                f"{decision.routed_model:<30}  {decision.message_preview}"
            )
        lines.append("─" * 60)
        lines.append(f"  Showing {len(history)} decisions")
        return Response(content="\n".join(lines))

    def _cmd_config(self, msg: InboundMessage, args: list[str]) -> Response:
        if not self._admin_user_ids or msg.user_id not in self._admin_user_ids:
            return Response(content="This command is admin-only.")
        if self._runtime_config_holder is None:
            return Response(content="Runtime config not available for this backend.")
        if not args:
            return self._cmd_config_show()
        if args[0] == "reset":
            return self._cmd_config_reset(args[1:])
        return self._cmd_config_set(args)

    def _cmd_config_show(self) -> Response:
        holder = self._runtime_config_holder
        assert holder is not None
        rc = holder.value
        lines = ["Runtime Config", "─" * 35]
        lines.append(f"  {'style':<20} {rc.style}")
        lines.append(f"  {'language':<20} {rc.language}")
        lines.append(f"  {'temperature':<20} {rc.temperature}")
        lines.append(f"  {'model':<20} {rc.model or '(persona default)'}")
        lines.append(f"  {'max_steps':<20} {rc.max_steps or '(model default)'}")
        extra = rc.extra_instructions or "(none)"
        lines.append(f"  {'extra_instructions':<20} {extra}")
        lines.append(f"  {'debounce_ms':<20} {rc.debounce_ms}")
        lines.append("─" * 35)
        return Response(content="\n".join(lines))

    def _cmd_config_set(self, args: list[str]) -> Response:
        from lyra.core.agent import _AGENTS_DIR
        from lyra.core.runtime_config import set_param

        holder = self._runtime_config_holder
        assert holder is not None
        rc = holder.value
        updates: list[str] = []
        for token in args:
            if "=" not in token:
                return Response(content=f"Invalid format: {token!r}. Use key=value.")
            key, _, value = token.partition("=")
            try:
                rc = set_param(rc, key.strip(), value.strip())
                updates.append(f"{key.strip()} = {value.strip()}")
            except ValueError as exc:
                return Response(content=str(exc))
        runtime_file = self._runtime_config_path or (_AGENTS_DIR / "lyra_runtime.toml")
        rc.save(runtime_file)
        old_rc = holder.value
        holder.value = rc
        # Propagate debounce_ms changes to Hub's live pools.
        if rc.debounce_ms != old_rc.debounce_ms and self._on_debounce_change:
            self._on_debounce_change(rc.debounce_ms)
        summary = f"Updated: {', '.join(updates)}\nSaved to {runtime_file.name}"
        return Response(content=summary)

    def _cmd_config_reset(self, args: list[str]) -> Response:
        from lyra.core.agent import _AGENTS_DIR
        from lyra.core.runtime_config import RuntimeConfig

        holder = self._runtime_config_holder
        assert holder is not None
        runtime_file = self._runtime_config_path or (_AGENTS_DIR / "lyra_runtime.toml")
        if not args:
            holder.value = RuntimeConfig()
            try:
                runtime_file.unlink(missing_ok=True)
            except OSError:
                pass
            return Response(content="Runtime config reset to defaults.")
        key = args[0]
        try:
            holder.value = RuntimeConfig.reset(holder.value, key)
        except ValueError as exc:
            return Response(content=str(exc))
        holder.value.save(runtime_file)
        return Response(content=f"Reset: {key} = (default). Saved.")

    async def _cmd_clear(self, pool: Pool | None) -> Response:
        """Clear conversation history and reset backend session."""
        if pool is None:
            return Response(content="No active session to clear.")
        pool.sdk_history.clear()
        pool.history.clear()
        await pool.reset_session()
        return Response(content="Conversation history cleared. Starting fresh.")
