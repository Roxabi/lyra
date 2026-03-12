"""Command router for Lyra hub (issue #66, updated #106).

Dispatches slash-prefixed messages to plugin handlers loaded by PluginLoader,
or to built-in handlers (/help, /circuit). SkillHandler and SKILL_REGISTRY removed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .circuit_breaker import CircuitRegistry
from .message import InboundMessage, Response
from .messages import MessageManager
from .plugin_loader import AsyncHandler, PluginLoader
from .pool import Pool

if TYPE_CHECKING:
    from lyra.core.runtime_config import RuntimeConfigHolder

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
        "/stop": CommandConfig(
            builtin=True, description="Cancel the current processing turn"
        ),
        "/config": CommandConfig(
            builtin=True, description="Show/set runtime config (admin-only)"
        ),
    }

    def __init__(
        self,
        plugin_loader: PluginLoader,
        enabled_plugins: list[str],
        builtins: dict[str, CommandConfig] | None = None,
        circuit_registry: CircuitRegistry | None = None,
        admin_user_ids: set[str] | None = None,
        msg_manager: MessageManager | None = None,
        runtime_config_holder: RuntimeConfigHolder | None = None,
        runtime_config_path: Path | None = None,
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

    async def dispatch(self, msg: InboundMessage, pool: Pool | None = None) -> Response:
        """Parse the command name + args and route to the appropriate handler."""
        parts = msg.text.split()
        command_name = parts[0].lower()
        args = parts[1:]

        if command_name == "/help":
            return self._help()

        if command_name == "/circuit":
            return self._circuit_status(msg)

        if command_name == "/stop":
            if pool is not None:
                pool.cancel()
            # reply sent by Pool._process_loop on CancelledError
            return Response(content="")

        if command_name == "/config":
            return self._cmd_config(msg, args)

        builtin = self._builtins.get(command_name)
        if builtin and builtin.builtin:
            return Response(
                content=f"Built-in command {command_name} is not yet implemented."
            )

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
        return await handler(msg, pool, args)

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
        holder.value = rc
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
