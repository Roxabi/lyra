"""Pattern matching utilities for command routing.

Extracted from command_router.py for maintainability.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage

    from .command_parser import CommandContext

BUNDLED_PATTERNS_CONFIG = (
    Path(__file__).resolve().parent.parent.parent / "config" / "patterns.toml"
)

BARE_URL_RE: re.Pattern[str] = re.compile(r"^https?://\S+$")

# Default command for bare URL rewriting
DEFAULT_BARE_URL_COMMAND = "vault-add"


def load_pattern_configs(path: Path | None = None) -> dict[str, dict]:
    """Load pattern rule configs from TOML. Falls back to bundled defaults."""
    target = path or BUNDLED_PATTERNS_CONFIG
    try:
        with open(target, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def rewrite_bare_url(
    msg: "InboundMessage",
    pattern_configs: dict[str, dict],
    command_context_class: type["CommandContext"],
) -> "InboundMessage":
    """Attach a synthetic command CommandContext for a bare URL message.

    Target command is read from pattern_configs ["bare_url"].command.
    """
    url = msg.text.strip()
    command = pattern_configs.get("bare_url", {}).get(
        "command", DEFAULT_BARE_URL_COMMAND
    )
    ctx = command_context_class(prefix="/", name=command, args=url, raw=msg.text)
    return replace(msg, command=ctx)


def is_bare_url(text: str, patterns: dict[str, bool]) -> bool:
    """Check if text is a bare URL and bare_url pattern is enabled."""
    if not patterns.get("bare_url", False):
        return False
    return bool(BARE_URL_RE.fullmatch(text.strip()))


def is_admin_only(description: str) -> bool:
    """Check if a command description marks it as admin-only."""
    return "(admin-only)" in description.lower()


def format_timeout_message(command_name: str, timeout: float) -> str:
    """Format a standardized timeout error message."""
    return f"Command {command_name} timed out after {timeout:.0f}s."


def check_command_conflicts(
    plugin_handlers: Mapping[str, object],
    builtins: Mapping[str, object],
) -> None:
    """Raise ValueError if any plugin command clashes with a builtin."""
    conflicts = set(plugin_handlers) & set(builtins)
    if conflicts:
        raise ValueError(
            f"Plugin command(s) clash with builtins: {sorted(conflicts)}. "
            "Rename the plugin command or remove the builtin."
        )


def format_unknown_command(command_name: str) -> str:
    """Format a standardized unknown command error message."""
    return f"Unknown command: {command_name}. Type /help for available commands."
