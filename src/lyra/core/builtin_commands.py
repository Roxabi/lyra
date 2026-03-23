"""Built-in command handlers extracted from CommandRouter (#298).

Stateless functions that receive dependencies as arguments and return Response.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .message import InboundMessage, Response

if TYPE_CHECKING:
    from collections.abc import Callable

    from lyra.llm.smart_routing import SmartRoutingDecorator

    from .circuit_breaker import CircuitRegistry
    from .commands.command_loader import CommandLoader
    from .messages import MessageManager
    from .runtime_config import RuntimeConfigHolder


def require_admin(msg: InboundMessage) -> "Response | None":
    """Return a denied Response if the user is not an admin, else None.

    Reads ``msg.is_admin`` set by ``Authenticator.resolve()``.

    Usage::

        if (denied := require_admin(msg)):
            return denied
    """
    if not msg.is_admin:
        return Response(content="This command is admin-only.")
    return None


def help_command(
    builtins: Mapping[str, object],
    session_handlers: "Mapping[str, object] | None",
    command_loader: "CommandLoader",
    enabled_plugins: list[str],
    msg_manager: "MessageManager | None",
) -> Response:
    """Return a listing of all available commands, grouped by section."""
    header = msg_manager.get("help_header") if msg_manager else "Available commands:"
    lines: list[str] = [header]
    lines.append("Commands:")
    for cmd_name, cfg in sorted(builtins.items()):
        desc = getattr(cfg, "description", "") or "(no description)"
        lines.append(f"  {cmd_name} — {desc}")
    if session_handlers:
        for cmd_name, entry in sorted(session_handlers.items()):
            desc = getattr(entry, "description", "") or "(no description)"
            lines.append(f"  {cmd_name} — {desc}")
    # Include processor registry commands (issue #363 — session_handlers is now empty)
    try:
        import lyra.core.processors  # noqa: F401 — trigger self-registration  # pyright: ignore[reportUnusedImport]
        from lyra.core.processor_registry import registry as _proc_registry

        proc_descs = _proc_registry.descriptions()
        if proc_descs:
            for cmd_name, desc in sorted(proc_descs.items()):
                lines.append(f"  {cmd_name} — {desc or '(no description)'}")
    except Exception:
        pass
    plugin_handlers = command_loader.get_commands(enabled_plugins)
    plugin_cmds = [cmd for cmd in sorted(plugin_handlers) if cmd not in builtins]
    if plugin_cmds:
        lines.append("Plugins:")
        plugin_descs = command_loader.get_command_descriptions(enabled_plugins)
        for cmd_name in plugin_cmds:
            desc = plugin_descs.get(cmd_name, "(plugin command)")
            lines.append(f"  {cmd_name} — {desc}")
    return Response(content="\n".join(lines))


def circuit_status(
    msg: InboundMessage,
    circuit_registry: "CircuitRegistry | None",
) -> Response:
    """Show circuit breaker status (admin-only)."""
    if denied := require_admin(msg):
        return denied
    if circuit_registry is None:
        return Response(content="Circuit breaker not configured.")
    all_status = circuit_registry.get_all_status()
    lines = ["Circuit Status", "─" * 38]
    for name, status in sorted(all_status.items()):
        if status.retry_after is not None:
            state_str = f"OPEN       retry in {int(status.retry_after)}s"
        else:
            state_str = f"{status.state.value.upper():<10} (ok)"
        lines.append(f"  {name:<12} {state_str}")
    lines.append("─" * 38)
    return Response(content="\n".join(lines))


def routing_status(
    msg: InboundMessage,
    smart_routing: "SmartRoutingDecorator | None",
) -> Response:
    """Show smart routing decisions (admin-only)."""
    if denied := require_admin(msg):
        return denied
    if smart_routing is None:
        return Response(content="Smart routing not configured.")
    history = smart_routing.history
    if not history:
        return Response(content="No routing decisions yet.")
    lines = ["Smart Routing — Recent Decisions", "─" * 60]
    for decision in reversed(history):
        ts = datetime.fromtimestamp(decision.timestamp, tz=timezone.utc)
        ts_str = ts.strftime("%H:%M:%S")
        lines.append(
            f"  {ts_str}  {decision.complexity.value:<8}  "
            f"{decision.routed_model:<30}  {decision.message_preview}"
        )
    lines.append("─" * 60)
    lines.append(f"  Showing {len(history)} decisions")
    return Response(content="\n".join(lines))


def config_command(  # noqa: PLR0913 — mirrors original DI surface
    msg: InboundMessage,
    args: list[str],
    runtime_config_holder: "RuntimeConfigHolder | None",
    runtime_config_path: Path | None,
    on_debounce_change: "Callable[[int], None] | None",
) -> Response:
    """Dispatch /config show/set/reset."""
    if denied := require_admin(msg):
        return denied
    if runtime_config_holder is None:
        return Response(content="Runtime config not available for this backend.")
    if not args:
        return _config_show(runtime_config_holder)
    if args[0] == "reset":
        return _config_reset(args[1:], runtime_config_holder, runtime_config_path)
    return _config_set(
        args, runtime_config_holder, runtime_config_path, on_debounce_change
    )


def _config_show(holder: "RuntimeConfigHolder") -> Response:
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


def _config_set(
    args: list[str],
    holder: "RuntimeConfigHolder",
    runtime_config_path: Path | None,
    on_debounce_change: "Callable[[int], None] | None",
) -> Response:
    from lyra.core.agent_config import _AGENTS_DIR
    from lyra.core.runtime_config import set_param

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
    runtime_file = runtime_config_path or (_AGENTS_DIR / "lyra_runtime.toml")
    rc.save(runtime_file)
    old_rc = holder.value
    holder.value = rc
    if rc.debounce_ms != old_rc.debounce_ms and on_debounce_change:
        on_debounce_change(rc.debounce_ms)
    summary = f"Updated: {', '.join(updates)}\nSaved to {runtime_file.name}"
    return Response(content=summary)


def _config_reset(
    args: list[str],
    holder: "RuntimeConfigHolder",
    runtime_config_path: Path | None,
) -> Response:
    from lyra.core.agent_config import _AGENTS_DIR
    from lyra.core.runtime_config import RuntimeConfig

    runtime_file = runtime_config_path or (_AGENTS_DIR / "lyra_runtime.toml")
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
