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

from .message import Message, Response, TextContent

log = logging.getLogger(__name__)

# Matches a message that starts with "/" followed by at least one word character.
_COMMAND_RE = re.compile(r"^/\w")

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


class SkillHandler:
    """Executes skill commands via CLI subprocesses."""

    @staticmethod
    async def execute(
        skill: str,
        action: str,
        args: list[str],
        timeout: float = 30.0,
    ) -> str:
        """Run the CLI for (skill, action) with positional args.

        Returns stdout as a string on success.
        Returns a user-facing error message on timeout or missing binary.
        """
        argv_prefix = SKILL_REGISTRY.get((skill, action))
        if argv_prefix is None:
            return f"Skill '{skill}/{action}' is not registered."

        cli_binary = argv_prefix[0]
        if shutil.which(cli_binary) is None:
            return f"'{cli_binary}' is not installed. Please install it first."

        full_argv = argv_prefix + args
        coro = SkillHandler._run_subprocess(full_argv)
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            coro.close()
            return "Command timed out. Please try again."
        except Exception as exc:  # noqa: BLE001
            coro.close()
            log.exception(
                "SkillHandler.execute failed for %s/%s: %s", skill, action, exc
            )
            return f"Command failed: {exc}"

    @staticmethod
    async def _run_subprocess(argv: list[str]) -> str:
        """Spawn subprocess and return stdout as a string."""
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()


class CommandRouter:
    """Routes slash commands to builtin handlers or CLI skill handlers."""

    def __init__(self, commands: dict[str, CommandConfig]) -> None:
        self.commands = commands

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

        unknown_reply = (
            f"Unknown command: {command_name}. Type /help for available commands."
        )

        cfg = self.commands.get(command_name)
        if cfg is None:
            return Response(content=unknown_reply)

        # Builtin commands other than /help are not yet defined — treat as unknown.
        if cfg.builtin:
            return self._help()

        # Skill-based command
        if cfg.skill and cfg.action:
            cli = cfg.cli or cfg.skill
            if shutil.which(cli) is None:
                return Response(
                    content=f"'{cli}' is not installed. Please install it first."
                )
            result = await SkillHandler.execute(cfg.skill, cfg.action, args)
            return Response(content=result)

        return Response(content=unknown_reply)

    # ------------------------------------------------------------------
    # Builtins
    # ------------------------------------------------------------------

    def _help(self) -> Response:
        """Return a listing of all registered commands with their descriptions."""
        lines: list[str] = ["Available commands:"]
        for cmd_name, cfg in sorted(self.commands.items()):
            desc = cfg.description or "(no description)"
            lines.append(f"  {cmd_name} — {desc}")
        return Response(content="\n".join(lines))
