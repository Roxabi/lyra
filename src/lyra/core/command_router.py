"""Command router for Lyra hub (issue #66).

Intercepts slash-prefixed messages before they reach agent.process(),
dispatches them to built-in handlers or CLI skill handlers, and returns
a Response that the hub sends back via the originating adapter.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass

from lyra.core.circuit_breaker import CircuitRegistry

from .message import Message, Response, TextContent

log = logging.getLogger(__name__)

# Matches a message that starts with "/" followed by at least one word character.
_COMMAND_RE = re.compile(r"^/\w")

# Maximum bytes of subprocess output returned to the user.
_MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB

# Skill registry: maps (skill, action) -> CLI argv prefix.
# Args from the user message are appended positionally.
SKILL_REGISTRY: dict[tuple[str, str], list[str]] = {
    ("echo", "echo"): ["echo"],
    ("google-workspace", "calendar-today"): [
        "gws",
        "calendar",
        "list",
        "--today",
        "--json",
    ],
}


@dataclass(frozen=True)
class CommandConfig:
    """Configuration for a single slash command, loaded from agent TOML."""

    skill: str | None = None
    action: str | None = None
    cli: str | None = None
    description: str = ""
    builtin: bool = False
    timeout: float = 30.0


class SkillHandler:
    """Executes skill commands via CLI subprocesses."""

    @staticmethod
    async def execute(
        skill: str,
        action: str,
        args: list[str],
        timeout: float = 30.0,
        cli: str | None = None,
    ) -> str:
        """Run the CLI for (skill, action) with positional args.

        Returns stdout as a string on success.
        Returns a user-facing error message on timeout or missing binary.

        cli: optional binary name override used for the existence check when
             the (skill, action) pair is not in SKILL_REGISTRY.
        """
        # Check the explicit cli override first so missing-binary errors are
        # surfaced even for skills not yet in the registry.
        if cli is not None and shutil.which(cli) is None:
            return f"'{cli}' is not installed. Please install it first."

        argv_prefix = SKILL_REGISTRY.get((skill, action))
        if argv_prefix is None:
            return f"Skill '{skill}/{action}' is not registered."

        cli_binary = argv_prefix[0]
        if shutil.which(cli_binary) is None:
            return f"'{cli_binary}' is not installed. Please install it first."

        full_argv = argv_prefix + args
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *full_argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            if proc is not None:
                proc.kill()
                await proc.wait()
            return "Command timed out. Please try again."
        except Exception:  # noqa: BLE001
            if proc is not None:
                proc.kill()
                await proc.wait()
            log.exception("SkillHandler.execute failed for %s/%s", skill, action)
            return "Command failed. Please contact the administrator."

        if proc.returncode != 0:
            log.warning(
                "subprocess %s exited with code %d: %s",
                full_argv[0],
                proc.returncode,
                stderr.decode(errors="replace")[:500],
            )
            return f"Command failed (exit code {proc.returncode})."

        output = stdout.decode()
        if len(output) > _MAX_OUTPUT_BYTES:
            return output[:_MAX_OUTPUT_BYTES] + "\n[output truncated]"
        return output


class CommandRouter:
    """Routes slash commands to builtin handlers or CLI skill handlers."""

    def __init__(
        self,
        commands: dict[str, CommandConfig],
        circuit_registry: CircuitRegistry | None = None,
        admin_user_ids: set[str] | None = None,
    ) -> None:
        self.commands = commands
        self._circuit_registry = circuit_registry
        self._admin_user_ids = admin_user_ids or set()

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def is_command(self, msg: Message) -> bool:
        """Return True if the message starts with '/' followed by a word char."""
        content = msg.content
        if isinstance(content, TextContent):
            text = content.text
        elif isinstance(content, str):
            text = content
        else:
            return False
        return bool(_COMMAND_RE.match(text))

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, msg: Message) -> Response:
        """Parse the command name + args and route to the appropriate handler."""
        content = msg.content
        if isinstance(content, TextContent):
            text = content.text
        else:
            text = str(content)

        parts = text.split()
        command_name = parts[0].lower()
        args = parts[1:]

        if command_name == "/help":
            return self._help()

        if command_name == "/circuit":
            return self._circuit_status(msg)

        unknown_reply = (
            f"Unknown command: {command_name}. Type /help for available commands."
        )

        cfg = self.commands.get(command_name)
        if cfg is None:
            return Response(content=unknown_reply)

        # Builtin commands other than /help are not yet defined — tell the user.
        if cfg.builtin:
            return Response(
                content=f"Built-in command {command_name} is not yet implemented."
            )

        # Skill-based command
        if cfg.skill and cfg.action:
            result = await SkillHandler.execute(
                cfg.skill, cfg.action, args, timeout=cfg.timeout, cli=cfg.cli
            )
            return Response(content=result)

        return Response(content=unknown_reply)

    # ------------------------------------------------------------------
    # Builtins
    # ------------------------------------------------------------------

    def _help(self) -> Response:
        """Return a listing of all registered commands with their descriptions."""
        lines: list[str] = ["Available commands:"]
        # Add /circuit as a built-in
        lines.append("  /circuit — Show circuit breaker status (admin-only)")
        for cmd_name, cfg in sorted(self.commands.items()):
            desc = cfg.description or "(no description)"
            lines.append(f"  {cmd_name} — {desc}")
        return Response(content="\n".join(lines))

    def _circuit_status(self, msg: Message) -> Response:
        """Return circuit status table (admin-only)."""
        # Check admin access using msg.user_id directly (already platform-prefixed)
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
